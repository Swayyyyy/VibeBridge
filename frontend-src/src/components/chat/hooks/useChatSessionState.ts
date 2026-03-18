import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { MutableRefObject } from 'react';
import { api, authenticatedFetch } from '../../../utils/api';
import type { ChatMessage, Provider } from '../types/types';
import type { Project, ProjectSession } from '../../../types/app';
import { safeLocalStorage } from '../utils/chatStorage';
import {
  convertSessionMessages,
  createCachedDiffCalculator,
  type DiffCalculator,
} from '../utils/messageTransforms';

const MESSAGES_PER_PAGE = 20;
const INITIAL_VISIBLE_MESSAGES = 100;

type PendingViewSession = {
  sessionId: string | null;
  startedAt: number;
};

type SessionLoadContext = {
  scopeKey: string;
  requestVersion: number;
};

const isTemporarySessionId = (sessionId: string | null | undefined) =>
  Boolean(
    sessionId &&
      (sessionId.startsWith('new-session-') || sessionId.startsWith('draft-')),
  );

const buildSessionScopeKey = (
  projectName: string,
  sessionId: string,
  provider: Provider | string = 'claude',
) => `${projectName}:${sessionId}:${provider || 'claude'}`;

interface UseChatSessionStateArgs {
  selectedProject: Project | null;
  selectedSession: ProjectSession | null;
  ws: WebSocket | null;
  sendMessage: (message: unknown) => void;
  autoScrollToBottom?: boolean;
  externalMessageUpdate?: number;
  processingSessions?: Set<string>;
  resetStreamingState: () => void;
  pendingViewSessionRef: MutableRefObject<PendingViewSession | null>;
}

interface ScrollRestoreState {
  height: number;
  top: number;
}

export function useChatSessionState({
  selectedProject,
  selectedSession,
  ws,
  sendMessage,
  autoScrollToBottom,
  externalMessageUpdate,
  processingSessions,
  resetStreamingState,
  pendingViewSessionRef,
}: UseChatSessionStateArgs) {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(() => {
    if (typeof window !== 'undefined' && selectedProject) {
      const saved = safeLocalStorage.getItem(`chat_messages_${selectedProject.name}`);
      if (saved) {
        try {
          return JSON.parse(saved) as ChatMessage[];
        } catch {
          console.error('Failed to parse saved chat messages, resetting');
          safeLocalStorage.removeItem(`chat_messages_${selectedProject.name}`);
          return [];
        }
      }
      return [];
    }
    return [];
  });
  const [isLoading, setIsLoading] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(selectedSession?.id || null);
  const [sessionMessages, setSessionMessages] = useState<any[]>([]);
  const [isLoadingSessionMessages, setIsLoadingSessionMessages] = useState(false);
  const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [totalMessages, setTotalMessages] = useState(0);
  const [isSystemSessionChange, setIsSystemSessionChange] = useState(false);
  const [canAbortSession, setCanAbortSession] = useState(false);
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false);
  const [tokenBudget, setTokenBudget] = useState<Record<string, unknown> | null>(null);
  const [visibleMessageCount, setVisibleMessageCount] = useState(INITIAL_VISIBLE_MESSAGES);
  const [claudeStatus, setClaudeStatus] = useState<{ text: string; tokens: number; can_interrupt: boolean } | null>(null);
  const [allMessagesLoaded, setAllMessagesLoaded] = useState(false);
  const [isLoadingAllMessages, setIsLoadingAllMessages] = useState(false);
  const [loadAllJustFinished, setLoadAllJustFinished] = useState(false);
  const [showLoadAllOverlay, setShowLoadAllOverlay] = useState(false);

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [searchTarget, setSearchTarget] = useState<{ timestamp?: string; uuid?: string; snippet?: string } | null>(null);
  const searchScrollActiveRef = useRef(false);
  const isLoadingSessionRef = useRef(false);
  const isLoadingMoreRef = useRef(false);
  const allMessagesLoadedRef = useRef(false);
  const topLoadLockRef = useRef(false);
  const pendingScrollRestoreRef = useRef<ScrollRestoreState | null>(null);
  const pendingInitialScrollRef = useRef(true);
  const messagesOffsetRef = useRef(0);
  const scrollPositionRef = useRef({ height: 0, top: 0 });
  const loadAllFinishedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadAllOverlayTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadedSessionKeyRef = useRef<string | null>(null);
  const activeSessionScopeKeyRef = useRef<string | null>(null);
  const sessionLoadVersionRef = useRef(0);

  const createDiff = useMemo<DiffCalculator>(() => createCachedDiffCalculator(), []);

  const beginReplaceSessionLoad = useCallback((scopeKey: string) => {
    activeSessionScopeKeyRef.current = scopeKey;
    sessionLoadVersionRef.current += 1;
    return sessionLoadVersionRef.current;
  }, []);

  const invalidateSessionLoads = useCallback((nextScopeKey: string | null = null) => {
    activeSessionScopeKeyRef.current = nextScopeKey;
    sessionLoadVersionRef.current += 1;
    return sessionLoadVersionRef.current;
  }, []);

  const getCurrentSessionLoadVersion = useCallback(() => {
    return sessionLoadVersionRef.current;
  }, []);

  const isSessionRequestCurrent = useCallback((scopeKey: string, requestVersion: number) => {
    return (
      activeSessionScopeKeyRef.current === scopeKey &&
      sessionLoadVersionRef.current === requestVersion
    );
  }, []);

  const loadSessionMessages = useCallback(
    async (
      projectName: string,
      sessionId: string,
      loadMore = false,
      provider: Provider | string = 'claude',
      requestContext?: SessionLoadContext,
    ) => {
      if (!projectName || !sessionId) {
        return [] as any[];
      }

      const normalizedProvider = provider || 'claude';
      const scopeKey =
        requestContext?.scopeKey || buildSessionScopeKey(projectName, sessionId, normalizedProvider);
      const requestVersion =
        requestContext?.requestVersion ?? sessionLoadVersionRef.current;
      const isCurrentRequest = () => isSessionRequestCurrent(scopeKey, requestVersion);

      const isInitialLoad = !loadMore;
      if (isInitialLoad) {
        if (isCurrentRequest()) {
          setIsLoadingSessionMessages(true);
        }
      } else {
        if (isCurrentRequest()) {
          setIsLoadingMoreMessages(true);
        }
      }

      try {
        const currentOffset = loadMore ? messagesOffsetRef.current : 0;
        if (!isCurrentRequest()) {
          return [] as any[];
        }
        const response = await (api.nodeAwareSessionMessages as any)(
          projectName,
          sessionId,
          MESSAGES_PER_PAGE,
          currentOffset,
          normalizedProvider,
        );
        if (!isCurrentRequest()) {
          return [] as any[];
        }
        if (!response.ok) {
          throw new Error('Failed to load session messages');
        }

        const data = await response.json();
        if (!isCurrentRequest()) {
          return [] as any[];
        }
        if (isInitialLoad && data.tokenUsage) {
          setTokenBudget(data.tokenUsage);
        }

        if (data.hasMore !== undefined) {
          const loadedCount = data.messages?.length || 0;
          setHasMoreMessages(Boolean(data.hasMore));
          setTotalMessages(Number(data.total || 0));
          messagesOffsetRef.current = currentOffset + loadedCount;
          return data.messages || [];
        }

        const messages = data.messages || [];
        setHasMoreMessages(false);
        setTotalMessages(messages.length);
        messagesOffsetRef.current = messages.length;
        return messages;
      } catch (error) {
        console.error('Error loading session messages:', error);
        return [];
      } finally {
        if (isInitialLoad) {
          if (isCurrentRequest()) {
            setIsLoadingSessionMessages(false);
          }
        } else {
          if (isCurrentRequest()) {
            setIsLoadingMoreMessages(false);
          }
        }
      }
    },
    [isSessionRequestCurrent],
  );

  const convertedMessages = useMemo(() => {
    return convertSessionMessages(sessionMessages);
  }, [sessionMessages]);

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }, []);

  const scrollToBottomAndReset = useCallback(() => {
    scrollToBottom();
    if (allMessagesLoaded) {
      setVisibleMessageCount(INITIAL_VISIBLE_MESSAGES);
      setAllMessagesLoaded(false);
      allMessagesLoadedRef.current = false;
    }
  }, [allMessagesLoaded, scrollToBottom]);

  const isNearBottom = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return false;
    }
    const { scrollTop, scrollHeight, clientHeight } = container;
    return scrollHeight - scrollTop - clientHeight < 50;
  }, []);

  const loadOlderMessages = useCallback(
    async (container: HTMLDivElement) => {
      if (!container || isLoadingMoreRef.current || isLoadingMoreMessages) {
        return false;
      }
      if (allMessagesLoadedRef.current) return false;
      if (!hasMoreMessages || !selectedSession || !selectedProject) {
        return false;
      }

      const sessionProvider = selectedSession.__provider || 'claude';
      const requestScopeKey = buildSessionScopeKey(
        selectedProject.name,
        selectedSession.id,
        sessionProvider,
      );
      const requestVersion = getCurrentSessionLoadVersion();

      isLoadingMoreRef.current = true;
      const previousScrollHeight = container.scrollHeight;
      const previousScrollTop = container.scrollTop;

      try {
        const moreMessages = await loadSessionMessages(
          selectedProject.name,
          selectedSession.id,
          true,
          sessionProvider,
          { scopeKey: requestScopeKey, requestVersion },
        );
        if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
          return false;
        }

        if (moreMessages.length === 0) {
          return false;
        }

        pendingScrollRestoreRef.current = {
          height: previousScrollHeight,
          top: previousScrollTop,
        };
        setSessionMessages((previous) => [...moreMessages, ...previous]);
        // Keep the rendered window in sync with top-pagination so newly loaded history becomes visible.
        setVisibleMessageCount((previousCount) => previousCount + moreMessages.length);
        return true;
      } finally {
        isLoadingMoreRef.current = false;
      }
    },
    [
      getCurrentSessionLoadVersion,
      hasMoreMessages,
      isLoadingMoreMessages,
      isSessionRequestCurrent,
      loadSessionMessages,
      selectedProject,
      selectedSession,
    ],
  );

  const handleScroll = useCallback(async () => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }

    const nearBottom = isNearBottom();
    setIsUserScrolledUp(!nearBottom);

    if (!allMessagesLoadedRef.current) {
      const scrolledNearTop = container.scrollTop < 100;
      if (!scrolledNearTop) {
        topLoadLockRef.current = false;
        return;
      }

      if (topLoadLockRef.current) {
        if (container.scrollTop > 20) {
          topLoadLockRef.current = false;
        }
        return;
      }

      const didLoad = await loadOlderMessages(container);
      if (didLoad) {
        topLoadLockRef.current = true;
      }
    }
  }, [isNearBottom, loadOlderMessages]);

  useLayoutEffect(() => {
    if (!pendingScrollRestoreRef.current || !scrollContainerRef.current) {
      return;
    }

    const { height, top } = pendingScrollRestoreRef.current;
    const container = scrollContainerRef.current;
    const newScrollHeight = container.scrollHeight;
    const scrollDiff = newScrollHeight - height;
    container.scrollTop = top + Math.max(scrollDiff, 0);
    pendingScrollRestoreRef.current = null;
  }, [chatMessages.length]);

  const prevSessionMessagesLengthRef = useRef(0);
  const isInitialLoadRef = useRef(true);

  useEffect(() => {
    if (!searchScrollActiveRef.current) {
      pendingInitialScrollRef.current = true;
      setVisibleMessageCount(INITIAL_VISIBLE_MESSAGES);
    }
    topLoadLockRef.current = false;
    pendingScrollRestoreRef.current = null;
    prevSessionMessagesLengthRef.current = 0;
    isInitialLoadRef.current = true;
    setIsUserScrolledUp(false);
  }, [selectedProject?.name, selectedSession?.id]);

  useEffect(() => {
    if (!pendingInitialScrollRef.current || !scrollContainerRef.current || isLoadingSessionMessages) {
      return;
    }

    if (chatMessages.length === 0) {
      pendingInitialScrollRef.current = false;
      return;
    }

    pendingInitialScrollRef.current = false;
    if (!searchScrollActiveRef.current) {
      setTimeout(() => {
        scrollToBottom();
      }, 200);
    }
  }, [chatMessages.length, isLoadingSessionMessages, scrollToBottom]);

  useEffect(() => {
    const loadMessages = async () => {
      if (selectedSession && selectedProject) {
        const provider =
          selectedSession.__provider ||
          ((localStorage.getItem('selected-provider') as Provider) || 'claude');
        const sessionKey = buildSessionScopeKey(selectedProject.name, selectedSession.id, provider);
        const requestVersion = beginReplaceSessionLoad(sessionKey);
        let didLoadMessages = false;
        isLoadingSessionRef.current = true;

        const sessionChanged = currentSessionId !== null && currentSessionId !== selectedSession.id;
        if (sessionChanged) {
          if (!isSystemSessionChange) {
            resetStreamingState();
            pendingViewSessionRef.current = null;
            setChatMessages([]);
            setSessionMessages([]);
            setClaudeStatus(null);
            setCanAbortSession(false);
          }

          messagesOffsetRef.current = 0;
          setHasMoreMessages(false);
          setTotalMessages(0);
          setVisibleMessageCount(INITIAL_VISIBLE_MESSAGES);
          setAllMessagesLoaded(false);
          allMessagesLoadedRef.current = false;
          setIsLoadingAllMessages(false);
          setIsLoadingSessionMessages(false);
          setIsLoadingMoreMessages(false);
          setLoadAllJustFinished(false);
          setShowLoadAllOverlay(false);
          if (loadAllOverlayTimerRef.current) clearTimeout(loadAllOverlayTimerRef.current);
          if (loadAllFinishedTimerRef.current) clearTimeout(loadAllFinishedTimerRef.current);
          setTokenBudget(null);
          setIsLoading(false);

          if (ws) {
            sendMessage({
              type: 'check-session-status',
              sessionId: selectedSession.id,
              provider,
            });
          }
        } else if (currentSessionId === null) {
          messagesOffsetRef.current = 0;
          setHasMoreMessages(false);
          setTotalMessages(0);
          setIsLoadingAllMessages(false);
          setIsLoadingSessionMessages(false);
          setIsLoadingMoreMessages(false);

          if (ws) {
            sendMessage({
              type: 'check-session-status',
              sessionId: selectedSession.id,
              provider,
            });
          }
        }

        // Skip loading if session+project+provider hasn't changed
        if (lastLoadedSessionKeyRef.current === sessionKey) {
          setTimeout(() => {
            if (isSessionRequestCurrent(sessionKey, requestVersion)) {
              isLoadingSessionRef.current = false;
            }
          }, 250);
          return;
        }

        setCurrentSessionId(selectedSession.id);

        if (!isSystemSessionChange) {
          const messages = await loadSessionMessages(
            selectedProject.name,
            selectedSession.id,
            false,
            provider,
            { scopeKey: sessionKey, requestVersion },
          );
          if (!isSessionRequestCurrent(sessionKey, requestVersion)) {
            return;
          }
          didLoadMessages = true;
          setSessionMessages(messages);
        } else {
          if (!isSessionRequestCurrent(sessionKey, requestVersion)) {
            return;
          }
          setIsSystemSessionChange(false);
        }

        // Update the last loaded session key
        if (!isSessionRequestCurrent(sessionKey, requestVersion)) {
          return;
        }
        if (didLoadMessages) {
          lastLoadedSessionKeyRef.current = sessionKey;
        }
        setTimeout(() => {
          if (isSessionRequestCurrent(sessionKey, requestVersion)) {
            isLoadingSessionRef.current = false;
          }
        }, 250);
      } else {
        invalidateSessionLoads(null);
        if (!isSystemSessionChange) {
          resetStreamingState();
          pendingViewSessionRef.current = null;
          setChatMessages([]);
          setSessionMessages([]);
          setClaudeStatus(null);
          setCanAbortSession(false);
          setIsLoading(false);
        }

        setCurrentSessionId(null);
        messagesOffsetRef.current = 0;
        setHasMoreMessages(false);
        setTotalMessages(0);
        setIsLoadingAllMessages(false);
        setIsLoadingSessionMessages(false);
        setIsLoadingMoreMessages(false);
        setTokenBudget(null);
        lastLoadedSessionKeyRef.current = null;
        isLoadingSessionRef.current = false;
      }
    };

    loadMessages();
  }, [
    beginReplaceSessionLoad,
    invalidateSessionLoads,
    // Intentionally exclude currentSessionId: this effect sets it and should not retrigger another full load.
    isSessionRequestCurrent,
    isSystemSessionChange,
    loadSessionMessages,
    pendingViewSessionRef,
    resetStreamingState,
    selectedProject,
    selectedSession?.id, // Only depend on session ID, not the entire object
    sendMessage,
    ws,
  ]);

  useEffect(() => {
    if (!externalMessageUpdate || !selectedSession || !selectedProject) {
      return;
    }

    const reloadExternalMessages = async () => {
      try {
        const provider =
          selectedSession.__provider ||
          ((localStorage.getItem('selected-provider') as Provider) || 'claude');
        const requestScopeKey = buildSessionScopeKey(
          selectedProject.name,
          selectedSession.id,
          provider,
        );
        const requestVersion = beginReplaceSessionLoad(requestScopeKey);

        if (allMessagesLoadedRef.current) {
          const response = await (api.nodeAwareSessionMessages as any)(
            selectedProject.name,
            selectedSession.id,
            null,
            0,
            provider,
          );

          if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
            return;
          }

          if (!response.ok) {
            throw new Error('Failed to reload full session messages');
          }

          const data = await response.json();
          if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
            return;
          }

          const allMessages = data.messages || data;
          const nextMessages = Array.isArray(allMessages) ? allMessages : [];
          setSessionMessages(nextMessages);
          setHasMoreMessages(false);
          setTotalMessages(nextMessages.length);
          messagesOffsetRef.current = nextMessages.length;
        } else {
          const messages = await loadSessionMessages(
            selectedProject.name,
            selectedSession.id,
            false,
            provider,
            { scopeKey: requestScopeKey, requestVersion },
          );
          if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
            return;
          }
          setSessionMessages(messages);
        }

        const shouldAutoScroll =
          Boolean(autoScrollToBottom) &&
          !isUserScrolledUp &&
          isNearBottom();
        if (shouldAutoScroll) {
          setTimeout(() => scrollToBottom(), 200);
        }
      } catch (error) {
        console.error('Error reloading messages from external update:', error);
      }
    };

    reloadExternalMessages();
  }, [
    autoScrollToBottom,
    beginReplaceSessionLoad,
    externalMessageUpdate,
    isNearBottom,
    isSessionRequestCurrent,
    isUserScrolledUp,
    loadSessionMessages,
    scrollToBottom,
    selectedProject,
    selectedSession,
  ]);

  // Detect search navigation target from selectedSession object reference change
  // This must be a separate effect because the loading effect depends on selectedSession?.id
  // which doesn't change when clicking a search result for the already-loaded session
  useEffect(() => {
    const session = selectedSession as Record<string, unknown> | null;
    const targetSnippet = session?.__searchTargetSnippet;
    const targetTimestamp = session?.__searchTargetTimestamp;
    if (typeof targetSnippet === 'string' && targetSnippet) {
      searchScrollActiveRef.current = true;
      setSearchTarget({
        snippet: targetSnippet,
        timestamp: typeof targetTimestamp === 'string' ? targetTimestamp : undefined,
      });
    }
  }, [selectedSession]);

  useEffect(() => {
    if (selectedSession?.id) {
      pendingViewSessionRef.current = null;
    }
  }, [pendingViewSessionRef, selectedSession?.id]);

  useEffect(() => {
    // Only sync sessionMessages to chatMessages when:
    // 1. Not currently loading (to avoid overwriting user's just-sent message)
    // 2. SessionMessages actually changed (including from non-empty to empty)
    // 3. Either it's initial load OR sessionMessages increased (new messages from server)
    if (
      sessionMessages.length !== prevSessionMessagesLengthRef.current &&
      !isLoading
    ) {
      // Only update if this is initial load, sessionMessages grew, or was cleared to empty
      if (isInitialLoadRef.current || sessionMessages.length === 0 || sessionMessages.length > prevSessionMessagesLengthRef.current) {
        setChatMessages(convertedMessages);
        isInitialLoadRef.current = false;
      }
      prevSessionMessagesLengthRef.current = sessionMessages.length;
    }
  }, [convertedMessages, sessionMessages.length, isLoading, setChatMessages]);

  useEffect(() => {
    if (selectedProject && chatMessages.length > 0) {
      safeLocalStorage.setItem(`chat_messages_${selectedProject.name}`, JSON.stringify(chatMessages));
    }
  }, [chatMessages, selectedProject]);

  // Scroll to search target message after messages are loaded
  useEffect(() => {
    if (!searchTarget || chatMessages.length === 0 || isLoadingSessionMessages) return;

    const target = searchTarget;
    // Clear immediately to prevent re-triggering
    setSearchTarget(null);

    const scrollToTarget = async () => {
      const sessionProvider = selectedSession?.__provider || 'claude';
      const requestScopeKey =
        selectedSession && selectedProject
          ? buildSessionScopeKey(selectedProject.name, selectedSession.id, sessionProvider)
          : null;
      const requestVersion =
        requestScopeKey && !allMessagesLoadedRef.current
          ? beginReplaceSessionLoad(requestScopeKey)
          : getCurrentSessionLoadVersion();

      // Always load all messages when navigating from search
      // (hasMoreMessages may not be set yet due to race with loading effect)
      if (!allMessagesLoadedRef.current && selectedSession && selectedProject && requestScopeKey) {
        try {
          const response = await (api.nodeAwareSessionMessages as any)(
            selectedProject.name,
            selectedSession.id,
            null,
            0,
            sessionProvider,
          );
          if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
            searchScrollActiveRef.current = false;
            return;
          }
          if (response.ok) {
            const data = await response.json();
            if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
              searchScrollActiveRef.current = false;
              return;
            }
            const allMessages = data.messages || data;
            const nextMessages = Array.isArray(allMessages) ? allMessages : [];
            setSessionMessages(nextMessages);
            setHasMoreMessages(false);
            setTotalMessages(nextMessages.length);
            messagesOffsetRef.current = nextMessages.length;
            setVisibleMessageCount(Infinity);
            setAllMessagesLoaded(true);
            allMessagesLoadedRef.current = true;
            // Wait for messages to render after state update
            await new Promise(resolve => setTimeout(resolve, 300));
            if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
              searchScrollActiveRef.current = false;
              return;
            }
          }
        } catch {
          // Fall through and scroll in current messages
        }
      }

      if (requestScopeKey && !isSessionRequestCurrent(requestScopeKey, requestVersion)) {
        searchScrollActiveRef.current = false;
        return;
      }
      setVisibleMessageCount(Infinity);

      // Retry finding the element in the DOM until React finishes rendering all messages
      const findAndScroll = (retriesLeft: number) => {
        if (requestScopeKey && !isSessionRequestCurrent(requestScopeKey, requestVersion)) {
          searchScrollActiveRef.current = false;
          return;
        }

        const container = scrollContainerRef.current;
        if (!container) return;

        let targetElement: Element | null = null;

        // Match by snippet text content (most reliable)
        if (target.snippet) {
          const cleanSnippet = target.snippet.replace(/^\.{3}/, '').replace(/\.{3}$/, '').trim();
          // Use a contiguous substring from the snippet (don't filter words, it breaks matching)
          const searchPhrase = cleanSnippet.slice(0, 80).toLowerCase().trim();

          if (searchPhrase.length >= 10) {
            const messageElements = container.querySelectorAll('.chat-message');
            for (const el of messageElements) {
              const text = (el.textContent || '').toLowerCase();
              if (text.includes(searchPhrase)) {
                targetElement = el;
                break;
              }
            }
          }
        }

        // Fallback to timestamp matching
        if (!targetElement && target.timestamp) {
          const targetDate = new Date(target.timestamp).getTime();
          const messageElements = container.querySelectorAll('[data-message-timestamp]');
          let closestDiff = Infinity;

          for (const el of messageElements) {
            const ts = el.getAttribute('data-message-timestamp');
            if (!ts) continue;
            const diff = Math.abs(new Date(ts).getTime() - targetDate);
            if (diff < closestDiff) {
              closestDiff = diff;
              targetElement = el;
            }
          }
        }

        if (targetElement) {
          targetElement.scrollIntoView({ block: 'center', behavior: 'smooth' });
          targetElement.classList.add('search-highlight-flash');
          setTimeout(() => targetElement?.classList.remove('search-highlight-flash'), 4000);
          searchScrollActiveRef.current = false;
        } else if (retriesLeft > 0) {
          setTimeout(() => findAndScroll(retriesLeft - 1), 200);
        } else {
          searchScrollActiveRef.current = false;
        }
      };

      // Start polling after a short delay to let React begin rendering
      setTimeout(() => findAndScroll(15), 150);
    };

    scrollToTarget();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatMessages.length, isLoadingSessionMessages, searchTarget]);

  useEffect(() => {
    if (!selectedProject || !selectedSession?.id || isTemporarySessionId(selectedSession.id)) {
      setTokenBudget(null);
      return;
    }

    const sessionProvider = selectedSession.__provider || 'claude';
    if (sessionProvider !== 'claude') {
      return;
    }

    const fetchInitialTokenUsage = async () => {
      const requestScopeKey = buildSessionScopeKey(
        selectedProject.name,
        selectedSession.id,
        sessionProvider,
      );
      const requestVersion = getCurrentSessionLoadVersion();

      try {
        const url = `/api/projects/${selectedProject.name}/sessions/${selectedSession.id}/token-usage`;
        const response = await authenticatedFetch(url);
        if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
          return;
        }
        if (response.ok) {
          const data = await response.json();
          if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) {
            return;
          }
          setTokenBudget(data);
        } else {
          setTokenBudget(null);
        }
      } catch (error) {
        console.error('Failed to fetch initial token usage:', error);
      }
    };

    fetchInitialTokenUsage();
  }, [
    getCurrentSessionLoadVersion,
    isSessionRequestCurrent,
    selectedProject,
    selectedSession?.id,
    selectedSession?.__provider,
  ]);

  const visibleMessages = useMemo(() => {
    if (chatMessages.length <= visibleMessageCount) {
      return chatMessages;
    }
    return chatMessages.slice(-visibleMessageCount);
  }, [chatMessages, visibleMessageCount]);

  useEffect(() => {
    if (!autoScrollToBottom && scrollContainerRef.current) {
      const container = scrollContainerRef.current;
      scrollPositionRef.current = {
        height: container.scrollHeight,
        top: container.scrollTop,
      };
    }
  });

  useEffect(() => {
    if (!scrollContainerRef.current || chatMessages.length === 0) {
      return;
    }

    if (isLoadingMoreRef.current || isLoadingMoreMessages || pendingScrollRestoreRef.current) {
      return;
    }

    if (searchScrollActiveRef.current) {
      return;
    }

    if (autoScrollToBottom) {
      if (!isUserScrolledUp) {
        setTimeout(() => scrollToBottom(), 50);
      }
      return;
    }

    const container = scrollContainerRef.current;
    const prevHeight = scrollPositionRef.current.height;
    const prevTop = scrollPositionRef.current.top;
    const newHeight = container.scrollHeight;
    const heightDiff = newHeight - prevHeight;

    if (heightDiff > 0 && prevTop > 0) {
      container.scrollTop = prevTop + heightDiff;
    }
  }, [autoScrollToBottom, chatMessages.length, isLoadingMoreMessages, isUserScrolledUp, scrollToBottom]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }

    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);

  useEffect(() => {
    const activeViewSessionId = selectedSession?.id || currentSessionId;
    if (!activeViewSessionId || !processingSessions) {
      return;
    }

    const shouldBeProcessing = processingSessions.has(activeViewSessionId);
    if (shouldBeProcessing && !isLoading) {
      setIsLoading(true);
      setCanAbortSession(true);
    }
  }, [currentSessionId, isLoading, processingSessions, selectedSession?.id]);

  // Show "Load all" overlay after a batch finishes loading, persist for 2s then hide
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = isLoadingMoreMessages;

    if (wasLoading && !isLoadingMoreMessages && hasMoreMessages) {
      if (loadAllOverlayTimerRef.current) clearTimeout(loadAllOverlayTimerRef.current);
      setShowLoadAllOverlay(true);
      loadAllOverlayTimerRef.current = setTimeout(() => {
        setShowLoadAllOverlay(false);
      }, 2000);
    }
    if (!hasMoreMessages && !isLoadingMoreMessages) {
      if (loadAllOverlayTimerRef.current) clearTimeout(loadAllOverlayTimerRef.current);
      setShowLoadAllOverlay(false);
    }
    return () => {
      if (loadAllOverlayTimerRef.current) clearTimeout(loadAllOverlayTimerRef.current);
    };
  }, [isLoadingMoreMessages, hasMoreMessages]);

  const loadAllMessages = useCallback(async () => {
    if (!selectedSession || !selectedProject) return;
    if (isLoadingAllMessages) return;
    const sessionProvider = selectedSession.__provider || 'claude';
    const requestScopeKey = buildSessionScopeKey(
      selectedProject.name,
      selectedSession.id,
      sessionProvider,
    );
    const requestVersion = beginReplaceSessionLoad(requestScopeKey);

    allMessagesLoadedRef.current = true;
    isLoadingMoreRef.current = true;
    setIsLoadingAllMessages(true);
    setShowLoadAllOverlay(true);

    const container = scrollContainerRef.current;
    const previousScrollHeight = container ? container.scrollHeight : 0;
    const previousScrollTop = container ? container.scrollTop : 0;

    try {
      const response = await (api.nodeAwareSessionMessages as any)(
        selectedProject.name,
        selectedSession.id,
        null,
        0,
        sessionProvider,
      );

      if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) return;

      if (response.ok) {
        const data = await response.json();
        if (!isSessionRequestCurrent(requestScopeKey, requestVersion)) return;
        const allMessages = data.messages || data;
        const nextMessages = Array.isArray(allMessages) ? allMessages : [];

        if (container) {
          pendingScrollRestoreRef.current = {
            height: previousScrollHeight,
            top: previousScrollTop,
          };
        }

        setSessionMessages(nextMessages);
        setHasMoreMessages(false);
        setTotalMessages(nextMessages.length);
        messagesOffsetRef.current = nextMessages.length;

        setVisibleMessageCount(Infinity);
        setAllMessagesLoaded(true);

        setLoadAllJustFinished(true);
        if (loadAllFinishedTimerRef.current) clearTimeout(loadAllFinishedTimerRef.current);
        loadAllFinishedTimerRef.current = setTimeout(() => {
          setLoadAllJustFinished(false);
          setShowLoadAllOverlay(false);
        }, 1000);
      } else {
        allMessagesLoadedRef.current = false;
        setShowLoadAllOverlay(false);
      }
    } catch (error) {
      console.error('Error loading all messages:', error);
      allMessagesLoadedRef.current = false;
      setShowLoadAllOverlay(false);
    } finally {
      isLoadingMoreRef.current = false;
      if (isSessionRequestCurrent(requestScopeKey, requestVersion)) {
        setIsLoadingAllMessages(false);
      }
    }
  }, [
    beginReplaceSessionLoad,
    isLoadingAllMessages,
    isSessionRequestCurrent,
    selectedProject,
    selectedSession,
  ]);

  const loadEarlierMessages = useCallback(() => {
    setVisibleMessageCount((previousCount) => previousCount + 100);
  }, []);

  return {
    chatMessages,
    setChatMessages,
    isLoading,
    setIsLoading,
    currentSessionId,
    setCurrentSessionId,
    sessionMessages,
    setSessionMessages,
    isLoadingSessionMessages,
    isLoadingMoreMessages,
    hasMoreMessages,
    totalMessages,
    isSystemSessionChange,
    setIsSystemSessionChange,
    canAbortSession,
    setCanAbortSession,
    isUserScrolledUp,
    setIsUserScrolledUp,
    tokenBudget,
    setTokenBudget,
    visibleMessageCount,
    visibleMessages,
    loadEarlierMessages,
    loadAllMessages,
    allMessagesLoaded,
    isLoadingAllMessages,
    loadAllJustFinished,
    showLoadAllOverlay,
    claudeStatus,
    setClaudeStatus,
    createDiff,
    scrollContainerRef,
    scrollToBottom,
    scrollToBottomAndReset,
    isNearBottom,
    handleScroll,
    loadSessionMessages,
  };
}
