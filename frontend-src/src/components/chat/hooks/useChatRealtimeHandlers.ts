import { useEffect, useRef } from 'react';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { decodeHtmlEntities, formatUsageLimitText } from '../utils/chatFormatting';
import { safeLocalStorage } from '../utils/chatStorage';
import type { ChatMessage, PendingPermissionRequest } from '../types/types';
import type { Project, ProjectSession, SessionProvider } from '../../../types/app';

type PendingViewSession = {
  sessionId: string | null;
  startedAt: number;
};

type LatestChatMessage = {
  type?: string;
  data?: any;
  sessionId?: string;
  requestId?: string;
  toolName?: string;
  input?: unknown;
  context?: unknown;
  error?: string;
  tool?: string;
  exitCode?: number;
  isProcessing?: boolean;
  actualSessionId?: string;
  [key: string]: any;
};

interface UseChatRealtimeHandlersArgs {
  latestMessage: LatestChatMessage | null;
  provider: SessionProvider;
  selectedProject: Project | null;
  selectedSession: ProjectSession | null;
  currentSessionId: string | null;
  setCurrentSessionId: (sessionId: string | null) => void;
  setChatMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setIsLoading: (loading: boolean) => void;
  setCanAbortSession: (canAbort: boolean) => void;
  setClaudeStatus: (status: { text: string; tokens: number; can_interrupt: boolean } | null) => void;
  setTokenBudget: (budget: Record<string, unknown> | null) => void;
  setIsSystemSessionChange: (isSystemSessionChange: boolean) => void;
  setPendingPermissionRequests: Dispatch<SetStateAction<PendingPermissionRequest[]>>;
  pendingViewSessionRef: MutableRefObject<PendingViewSession | null>;
  streamBufferRef: MutableRefObject<string>;
  streamTimerRef: MutableRefObject<number | null>;
  onSessionInactive?: (sessionId?: string | null) => void;
  onSessionProcessing?: (sessionId?: string | null) => void;
  onSessionNotProcessing?: (sessionId?: string | null) => void;
  onReplaceTemporarySession?: (sessionId?: string | null) => void;
  onNavigateToSession?: (sessionId: string) => void;
  onWebSocketReconnect?: () => void;
}

const appendStreamingChunk = (
  setChatMessages: Dispatch<SetStateAction<ChatMessage[]>>,
  chunk: string,
  newline = false,
) => {
  if (!chunk) {
    return;
  }

  setChatMessages((previous) => {
    const updated = [...previous];
    const lastIndex = updated.length - 1;
    const last = updated[lastIndex];
    if (last && last.type === 'assistant' && !last.isToolUse && last.isStreaming) {
      const nextContent = newline
        ? last.content
          ? `${last.content}\n${chunk}`
          : chunk
        : `${last.content || ''}${chunk}`;
      // Clone the message instead of mutating in place so React can reliably detect state updates.
      updated[lastIndex] = { ...last, content: nextContent };
    } else {
      updated.push({ type: 'assistant', content: chunk, timestamp: new Date(), isStreaming: true });
    }
    return updated;
  });
};

const finalizeStreamingMessage = (setChatMessages: Dispatch<SetStateAction<ChatMessage[]>>) => {
  setChatMessages((previous) => {
    const updated = [...previous];
    const lastIndex = updated.length - 1;
    const last = updated[lastIndex];
    if (last && last.type === 'assistant' && last.isStreaming) {
      // Clone the message instead of mutating in place so React can reliably detect state updates.
      updated[lastIndex] = { ...last, isStreaming: false };
    }
    return updated;
  });
};

const scheduleSessionMetadataRefresh = (
  onWebSocketReconnect?: () => void,
  delayMs = 150,
) => {
  if (typeof window === 'undefined') {
    return;
  }

  window.setTimeout(() => {
    void window.refreshProjects?.();
    onWebSocketReconnect?.();
  }, delayMs);
};

const normalizeCodexCommandToolInput = (command: unknown, cwd?: unknown) => {
  if (typeof command === 'string') {
    const trimmed = command.trim();
    if (!trimmed) {
      return null;
    }

    const payload: Record<string, unknown> = { command: trimmed };
    if (typeof cwd === 'string' && cwd.trim()) {
      payload.cwd = cwd.trim();
    }
    return payload;
  }

  if (Array.isArray(command)) {
    const joined = command
      .filter((part) => part !== null && part !== undefined)
      .map((part) => String(part).trim())
      .filter(Boolean)
      .join(' ');

    if (!joined) {
      return null;
    }

    const payload: Record<string, unknown> = { command: joined };
    if (typeof cwd === 'string' && cwd.trim()) {
      payload.cwd = cwd.trim();
    }
    return payload;
  }

  if (command && typeof command === 'object') {
    return command;
  }

  if (command === null || command === undefined) {
    return null;
  }

  return { command: String(command) };
};

const normalizeCodexCommandToolResult = (output: unknown, exitCode: unknown) => {
  if (output === null || output === undefined || output === '') {
    return null;
  }

  const normalizedOutput = typeof output === 'string'
    ? output
    : JSON.stringify(output, null, 2);

  return {
    content: normalizedOutput,
    isError: typeof exitCode === 'number' ? exitCode !== 0 : false,
  };
};

const appendCompactionStatus = (
  setChatMessages: Dispatch<SetStateAction<ChatMessage[]>>,
  status: 'compacting' | 'compacted',
  summary?: unknown,
) => {
  const normalizedSummary =
    typeof summary === 'string' ? decodeHtmlEntities(summary) : '';

  setChatMessages((previous) => {
    const updated = [...previous];
    const lastIndex = updated.length - 1;
    const last = updated[lastIndex];

    if (last?.isCompactionStatus && last.compactionState === status) {
      if (
        status === 'compacted' &&
        normalizedSummary &&
        last.compactionSummary !== normalizedSummary
      ) {
        updated[lastIndex] = {
          ...last,
          content: normalizedSummary,
          compactionSummary: normalizedSummary,
        };
        return updated;
      }
      return previous;
    }

    updated.push({
      type: 'assistant',
      content: normalizedSummary,
      timestamp: new Date(),
      isCompactionStatus: true,
      compactionState: status,
      compactionSummary: normalizedSummary || undefined,
    });
    return updated;
  });
};

const getClaudeContentPartType = (part: unknown): string | null => {
  if (!part || typeof part !== 'object') {
    return null;
  }

  const candidate = part as Record<string, any>;
  if (typeof candidate.type === 'string' && candidate.type.trim()) {
    return candidate.type;
  }

  if (typeof candidate.text === 'string') {
    return 'text';
  }

  if (typeof candidate.thinking === 'string') {
    return 'thinking';
  }

  if (candidate.tool_use_id) {
    return 'tool_result';
  }

  if (candidate.id && candidate.name) {
    return 'tool_use';
  }

  return null;
};

export function useChatRealtimeHandlers({
  latestMessage,
  provider,
  selectedProject,
  selectedSession,
  currentSessionId,
  setCurrentSessionId,
  setChatMessages,
  setIsLoading,
  setCanAbortSession,
  setClaudeStatus,
  setTokenBudget,
  setIsSystemSessionChange,
  setPendingPermissionRequests,
  pendingViewSessionRef,
  streamBufferRef,
  streamTimerRef,
  onSessionInactive,
  onSessionProcessing,
  onSessionNotProcessing,
  onReplaceTemporarySession,
  onNavigateToSession,
  onWebSocketReconnect,
}: UseChatRealtimeHandlersArgs) {
  const lastProcessedMessageRef = useRef<LatestChatMessage | null>(null);

  useEffect(() => {
    if (!latestMessage) {
      return;
    }

    // Guard against duplicate processing when dependency updates occur without a new message object.
    if (lastProcessedMessageRef.current === latestMessage) {
      return;
    }
    lastProcessedMessageRef.current = latestMessage;

    const messageData = latestMessage.data?.message || latestMessage.data;
    const structuredMessageData =
      messageData && typeof messageData === 'object' ? (messageData as Record<string, any>) : null;
    const rawStructuredData =
      latestMessage.data && typeof latestMessage.data === 'object'
        ? (latestMessage.data as Record<string, any>)
        : null;
    const structuredMessageRole =
      structuredMessageData?.role ||
      (structuredMessageData?.type === 'assistant' || structuredMessageData?.type === 'user'
        ? structuredMessageData.type
        : null);
    const messageType = String(latestMessage.type);

    const globalMessageTypes = ['projects_updated', 'taskmaster-project-updated', 'session-created', 'websocket-reconnected'];
    const isGlobalMessage = globalMessageTypes.includes(messageType);
    const lifecycleMessageTypes = new Set([
      'claude-complete',
      'codex-complete',
      'session-aborted',
      'claude-error',
      'codex-error',
      'error',
    ]);

    const isClaudeSystemInit =
      latestMessage.type === 'claude-response' &&
      structuredMessageData &&
      structuredMessageData.type === 'system' &&
      structuredMessageData.subtype === 'init';

    const systemInitSessionId = isClaudeSystemInit
      ? structuredMessageData?.session_id
      : null;

    const activeViewSessionId =
      selectedSession?.id || currentSessionId || pendingViewSessionRef.current?.sessionId || null;
    const hasPendingUnboundSession =
      Boolean(pendingViewSessionRef.current) && !pendingViewSessionRef.current?.sessionId;
    const isSystemInitForView =
      systemInitSessionId && (!activeViewSessionId || systemInitSessionId === activeViewSessionId);
    const shouldBypassSessionFilter = isGlobalMessage || Boolean(isSystemInitForView);
    const isLifecycleMessage = lifecycleMessageTypes.has(messageType);
    const isUnscopedError =
      !latestMessage.sessionId &&
      pendingViewSessionRef.current &&
      !pendingViewSessionRef.current.sessionId &&
      (latestMessage.type === 'claude-error' ||
        latestMessage.type === 'codex-error' ||
        latestMessage.type === 'error');

    const handleBackgroundLifecycle = (sessionId?: string) => {
      if (!sessionId) {
        return;
      }
      onSessionInactive?.(sessionId);
      onSessionNotProcessing?.(sessionId);
    };

    const collectSessionIds = (...sessionIds: Array<string | null | undefined>) =>
      Array.from(
        new Set(
          sessionIds.filter((sessionId): sessionId is string => typeof sessionId === 'string' && sessionId.length > 0),
        ),
      );

    const clearLoadingIndicators = () => {
      setIsLoading(false);
      setCanAbortSession(false);
      setClaudeStatus(null);
    };

    const clearPendingViewSession = (resolvedSessionId?: string | null) => {
      const pendingSession = pendingViewSessionRef.current;
      if (!pendingSession) {
        return;
      }

      // If the in-view request never received a concrete session ID (or this terminal event
      // resolves the same pending session), clear it to avoid stale "in-flight" UI state.
      if (!pendingSession.sessionId || !resolvedSessionId || pendingSession.sessionId === resolvedSessionId) {
        pendingViewSessionRef.current = null;
      }
    };

    const flushStreamingState = () => {
      if (streamTimerRef.current) {
        clearTimeout(streamTimerRef.current);
        streamTimerRef.current = null;
      }
      const pendingChunk = streamBufferRef.current;
      streamBufferRef.current = '';
      appendStreamingChunk(setChatMessages, pendingChunk, false);
      finalizeStreamingMessage(setChatMessages);
    };

    const markSessionsAsCompleted = (...sessionIds: Array<string | null | undefined>) => {
      const normalizedSessionIds = collectSessionIds(...sessionIds);
      normalizedSessionIds.forEach((sessionId) => {
        onSessionInactive?.(sessionId);
        onSessionNotProcessing?.(sessionId);
      });
    };

    const finalizeLifecycleForCurrentView = (...sessionIds: Array<string | null | undefined>) => {
      const pendingSessionId = typeof window !== 'undefined' ? sessionStorage.getItem('pendingSessionId') : null;
      const resolvedSessionIds = collectSessionIds(...sessionIds, pendingSessionId, pendingViewSessionRef.current?.sessionId);
      const resolvedPrimarySessionId = resolvedSessionIds[0] || null;

      flushStreamingState();
      clearLoadingIndicators();
      markSessionsAsCompleted(...resolvedSessionIds);
      setPendingPermissionRequests([]);
      clearPendingViewSession(resolvedPrimarySessionId);
    };

    if (!shouldBypassSessionFilter) {
      if (!activeViewSessionId) {
        if (latestMessage.sessionId && isLifecycleMessage && !hasPendingUnboundSession) {
          handleBackgroundLifecycle(latestMessage.sessionId);
          return;
        }
        if (!isUnscopedError && !hasPendingUnboundSession) {
          return;
        }
      }

      if (!latestMessage.sessionId && !isUnscopedError && !hasPendingUnboundSession) {
        return;
      }

      if (latestMessage.sessionId !== activeViewSessionId) {
        const shouldTreatAsPendingViewLifecycle =
          !activeViewSessionId &&
          hasPendingUnboundSession &&
          latestMessage.sessionId &&
          isLifecycleMessage;

        if (!shouldTreatAsPendingViewLifecycle) {
          if (latestMessage.sessionId && isLifecycleMessage) {
            handleBackgroundLifecycle(latestMessage.sessionId);
          }
          return;
        }
      }
    }

    switch (latestMessage.type) {
      case 'session-created':
        if (latestMessage.sessionId && !currentSessionId) {
          sessionStorage.setItem('pendingSessionId', latestMessage.sessionId);
          if (pendingViewSessionRef.current && !pendingViewSessionRef.current.sessionId) {
            pendingViewSessionRef.current.sessionId = latestMessage.sessionId;
          }

          setCurrentSessionId(latestMessage.sessionId);
          setIsSystemSessionChange(true);
          onReplaceTemporarySession?.(latestMessage.sessionId);
          onNavigateToSession?.(latestMessage.sessionId);

          setPendingPermissionRequests((previous) =>
            previous.map((request) =>
              request.sessionId ? request : { ...request, sessionId: latestMessage.sessionId },
            ),
          );

          scheduleSessionMetadataRefresh(onWebSocketReconnect, 250);
        }
        break;

      case 'websocket-reconnected':
        // WebSocket dropped and reconnected — re-fetch session history to catch up on missed messages
        onWebSocketReconnect?.();
        break;

      case 'token-budget':
        if (latestMessage.data) {
          setTokenBudget(latestMessage.data);
        }
        break;

      case 'claude-response': {
        if (messageData && typeof messageData === 'object' && messageData.type) {
          if (messageData.type === 'content_block_delta' && messageData.delta?.text) {
            const decodedText = decodeHtmlEntities(messageData.delta.text);
            streamBufferRef.current += decodedText;
            if (!streamTimerRef.current) {
              streamTimerRef.current = window.setTimeout(() => {
                const chunk = streamBufferRef.current;
                streamBufferRef.current = '';
                streamTimerRef.current = null;
                appendStreamingChunk(setChatMessages, chunk, false);
              }, 100);
            }
            return;
          }

          if (messageData.type === 'content_block_stop') {
            if (streamTimerRef.current) {
              clearTimeout(streamTimerRef.current);
              streamTimerRef.current = null;
            }
            const chunk = streamBufferRef.current;
            streamBufferRef.current = '';
            appendStreamingChunk(setChatMessages, chunk, false);
            finalizeStreamingMessage(setChatMessages);
            return;
          }
        }

        if (
          structuredMessageData?.type === 'system' &&
          structuredMessageData.subtype === 'init' &&
          structuredMessageData.session_id &&
          currentSessionId &&
          structuredMessageData.session_id !== currentSessionId &&
          isSystemInitForView
        ) {
          setIsSystemSessionChange(true);
          onNavigateToSession?.(structuredMessageData.session_id);
          return;
        }

        if (
          structuredMessageData?.type === 'system' &&
          structuredMessageData.subtype === 'init' &&
          structuredMessageData.session_id &&
          !currentSessionId &&
          isSystemInitForView
        ) {
          setIsSystemSessionChange(true);
          onNavigateToSession?.(structuredMessageData.session_id);
          return;
        }

        if (
          structuredMessageData?.type === 'system' &&
          structuredMessageData.subtype === 'init' &&
          structuredMessageData.session_id &&
          currentSessionId &&
          structuredMessageData.session_id === currentSessionId &&
          isSystemInitForView
        ) {
          return;
        }

        if (structuredMessageData && Array.isArray(structuredMessageData.content)) {
          const parentToolUseId = rawStructuredData?.parentToolUseId;

          structuredMessageData.content.forEach((part: any) => {
            const partType = getClaudeContentPartType(part);

            if (partType === 'tool_use') {
              const toolInput = part.input ? JSON.stringify(part.input, null, 2) : '';

              // Check if this is a child tool from a subagent
              if (parentToolUseId) {
                setChatMessages((previous) =>
                  previous.map((message) => {
                    if (message.toolId === parentToolUseId && message.isSubagentContainer) {
                      const childTool = {
                        toolId: part.id,
                        toolName: part.name,
                        toolInput: part.input,
                        toolResult: null,
                        timestamp: new Date(),
                      };
                      const existingChildren = message.subagentState?.childTools || [];
                      return {
                        ...message,
                        subagentState: {
                          childTools: [...existingChildren, childTool],
                          currentToolIndex: existingChildren.length,
                          isComplete: false,
                        },
                      };
                    }
                    return message;
                  }),
                );
                return;
              }

              // Check if this is a Task tool (subagent container)
              const isSubagentContainer = part.name === 'Task';

              setChatMessages((previous) => [
                ...previous,
                {
                  type: 'assistant',
                  content: '',
                  timestamp: new Date(),
                  isToolUse: true,
                  toolName: part.name,
                  toolInput,
                  toolId: part.id,
                  toolResult: null,
                  isSubagentContainer,
                  subagentState: isSubagentContainer
                    ? { childTools: [], currentToolIndex: -1, isComplete: false }
                    : undefined,
                },
              ]);
              return;
            }

            if (partType === 'text' && part.text?.trim()) {
              let content = decodeHtmlEntities(part.text);
              content = formatUsageLimitText(content);
              setChatMessages((previous) => [
                ...previous,
                {
                  type: 'assistant',
                  content,
                  timestamp: new Date(),
                },
              ]);
            }
          });
        } else if (structuredMessageData && typeof structuredMessageData.content === 'string' && structuredMessageData.content.trim()) {
          let content = decodeHtmlEntities(structuredMessageData.content);
          content = formatUsageLimitText(content);
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content,
              timestamp: new Date(),
            },
          ]);
        }

        const structuredContentParts = structuredMessageData?.content;
        if (structuredMessageRole === 'user' && Array.isArray(structuredContentParts)) {
          const parentToolUseId = rawStructuredData?.parentToolUseId;

          structuredContentParts.forEach((part: any) => {
            if (getClaudeContentPartType(part) !== 'tool_result') {
              return;
            }

            setChatMessages((previous) =>
              previous.map((message) => {
                // Handle child tool results (route to parent's subagentState)
                if (parentToolUseId && message.toolId === parentToolUseId && message.isSubagentContainer) {
                  return {
                    ...message,
                    subagentState: {
                      ...message.subagentState!,
                      childTools: message.subagentState!.childTools.map((child) => {
                        if (child.toolId === part.tool_use_id) {
                          return {
                            ...child,
                            toolResult: {
                              content: part.content,
                              isError: part.is_error,
                              timestamp: new Date(),
                            },
                          };
                        }
                        return child;
                      }),
                    },
                  };
                }

                // Handle normal tool results (including parent Task tool completion)
                if (message.isToolUse && message.toolId === part.tool_use_id) {
                  const result = {
                    ...message,
                    toolResult: {
                      content: part.content,
                      isError: part.is_error,
                      timestamp: new Date(),
                    },
                  };
                  // Mark subagent as complete when parent Task receives its result
                  if (message.isSubagentContainer && message.subagentState) {
                    result.subagentState = {
                      ...message.subagentState,
                      isComplete: true,
                    };
                  }
                  return result;
                }
                return message;
              }),
            );
          });
        }
        break;
      }

      case 'claude-output': {
        const cleaned = String(latestMessage.data || '');
        if (cleaned.trim()) {
          streamBufferRef.current += streamBufferRef.current ? `\n${cleaned}` : cleaned;
          if (!streamTimerRef.current) {
            streamTimerRef.current = window.setTimeout(() => {
              const chunk = streamBufferRef.current;
              streamBufferRef.current = '';
              streamTimerRef.current = null;
              appendStreamingChunk(setChatMessages, chunk, true);
            }, 100);
          }
        }
        break;
      }

      case 'claude-interactive-prompt':
        // Interactive prompts are parsed/rendered as text in the UI.
        // Normalize to string to keep ChatMessage.content shape consistent.
        {
          const interactiveContent =
            typeof latestMessage.data === 'string'
              ? latestMessage.data
              : JSON.stringify(latestMessage.data ?? '', null, 2);
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content: interactiveContent,
              timestamp: new Date(),
              isInteractivePrompt: true,
            },
          ]);
        }
        break;

      case 'claude-permission-request':
      case 'codex-permission-request':
        if (
          !latestMessage.requestId ||
          (latestMessage.type === 'claude-permission-request' && provider !== 'claude') ||
          (latestMessage.type === 'codex-permission-request' && provider !== 'codex')
        ) {
          break;
        }
        {
          const requestId = latestMessage.requestId;

          setPendingPermissionRequests((previous) => {
            if (previous.some((request) => request.requestId === requestId)) {
              return previous;
            }
            return [
              ...previous,
              {
                requestId,
                toolName: latestMessage.toolName || 'UnknownTool',
                input: latestMessage.input,
                context: latestMessage.context,
                sessionId: latestMessage.sessionId || null,
                receivedAt: new Date(),
              },
            ];
          });
        }

        setIsLoading(true);
        setCanAbortSession(true);
        setClaudeStatus({
          text: 'Waiting for permission',
          tokens: 0,
          can_interrupt: true,
        });
        break;

      case 'claude-permission-cancelled':
      case 'codex-permission-cancelled':
        if (!latestMessage.requestId) {
          break;
        }
        setPendingPermissionRequests((previous) =>
          previous.filter((request) => request.requestId !== latestMessage.requestId),
        );
        break;

      case 'claude-error':
        finalizeLifecycleForCurrentView(latestMessage.sessionId, currentSessionId, selectedSession?.id);
        setChatMessages((previous) => [
          ...previous,
          {
            type: 'error',
            content: `Error: ${latestMessage.error}`,
            timestamp: new Date(),
          },
        ]);
        break;

      case 'claude-complete': {
        const pendingSessionId = sessionStorage.getItem('pendingSessionId');
        const completedSessionId =
          latestMessage.sessionId || currentSessionId || pendingSessionId;

        finalizeLifecycleForCurrentView(
          completedSessionId,
          currentSessionId,
          selectedSession?.id,
          pendingSessionId,
        );

        if (pendingSessionId && !currentSessionId && latestMessage.exitCode === 0) {
          setCurrentSessionId(pendingSessionId);
          setIsSystemSessionChange(true);
          onNavigateToSession?.(pendingSessionId);
          sessionStorage.removeItem('pendingSessionId');
          console.log('New session complete, ID set to:', pendingSessionId);
        }

        if (selectedProject && latestMessage.exitCode === 0) {
          safeLocalStorage.removeItem(`chat_messages_${selectedProject.name}`);
        }
        if (latestMessage.exitCode === 0) {
          onWebSocketReconnect?.();
          scheduleSessionMetadataRefresh(onWebSocketReconnect);
        }
        break;
      }

      case 'codex-response': {
        const codexData = latestMessage.data;
        if (!codexData) {
          break;
        }

        if (codexData.type === 'item') {
          switch (codexData.itemType) {
            case 'agent_message':
              if (codexData.message?.content?.trim()) {
                const content = decodeHtmlEntities(codexData.message.content);
                setChatMessages((previous) => [
                  ...previous,
                  {
                    type: 'assistant',
                    content,
                    timestamp: new Date(),
                  },
                ]);
              }
              break;

            case 'reasoning':
              if (codexData.message?.content?.trim()) {
                const content = decodeHtmlEntities(codexData.message.content);
                setChatMessages((previous) => [
                  ...previous,
                  {
                    type: 'assistant',
                    content,
                    timestamp: new Date(),
                    isThinking: true,
                  },
                ]);
              }
              break;

            case 'command_execution':
              {
                const toolInput = normalizeCodexCommandToolInput(
                  codexData.command ?? codexData.parsedCommand,
                  codexData.cwd ?? codexData.workdir,
                );

                if (!toolInput) {
                  break;
                }

                setChatMessages((previous) => [
                  ...previous,
                  {
                    type: 'assistant',
                    content: '',
                    timestamp: new Date(),
                    isToolUse: true,
                    toolName: 'Bash',
                    toolInput,
                    toolResult: normalizeCodexCommandToolResult(
                      codexData.output,
                      codexData.exitCode,
                    ),
                    exitCode: codexData.exitCode,
                  },
                ]);
              }
              break;

            case 'file_change':
              if (codexData.changes?.length > 0) {
                const changesList = codexData.changes
                  .map((change: { kind: string; path: string }) => `${change.kind}: ${change.path}`)
                  .join('\n');
                setChatMessages((previous) => [
                  ...previous,
                  {
                    type: 'assistant',
                    content: '',
                    timestamp: new Date(),
                    isToolUse: true,
                    toolName: 'FileChanges',
                    toolInput: changesList,
                    toolResult: {
                      content: `Status: ${codexData.status}`,
                      isError: false,
                    },
                  },
                ]);
              }
              break;

            case 'compaction_status':
              appendCompactionStatus(
                setChatMessages,
                codexData.status === 'compacting' ? 'compacting' : 'compacted',
                codexData.summary ?? codexData.message?.content ?? codexData.content,
              );
              break;

            case 'mcp_tool_call':
              setChatMessages((previous) => [
                ...previous,
                {
                  type: 'assistant',
                  content: '',
                  timestamp: new Date(),
                  isToolUse: true,
                  toolName: `${codexData.server}:${codexData.tool}`,
                  toolInput: JSON.stringify(codexData.arguments, null, 2),
                  toolResult: codexData.result
                    ? JSON.stringify(codexData.result, null, 2)
                    : codexData.error?.message || null,
                },
              ]);
              break;

            case 'error':
              if (codexData.message?.content) {
                setChatMessages((previous) => [
                  ...previous,
                  {
                    type: 'error',
                    content: codexData.message.content,
                    timestamp: new Date(),
                  },
                ]);
              }
              break;

            default:
              console.log('[Codex] Unhandled item type:', codexData.itemType, codexData);
          }
        }

        if (codexData.type === 'turn_complete') {
          finalizeLifecycleForCurrentView(latestMessage.sessionId, currentSessionId, selectedSession?.id);
        }

        if (codexData.type === 'turn_failed') {
          finalizeLifecycleForCurrentView(latestMessage.sessionId, currentSessionId, selectedSession?.id);
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'error',
              content: codexData.error?.message || 'Turn failed',
              timestamp: new Date(),
            },
          ]);
        }
        break;
      }

      case 'codex-complete': {
        const codexPendingSessionId = sessionStorage.getItem('pendingSessionId');
        const codexActualSessionId = latestMessage.actualSessionId || codexPendingSessionId;
        const codexCompletedSessionId =
          latestMessage.sessionId || currentSessionId || codexPendingSessionId;

        finalizeLifecycleForCurrentView(
          codexCompletedSessionId,
          codexActualSessionId,
          currentSessionId,
          selectedSession?.id,
          codexPendingSessionId,
        );

        if (codexPendingSessionId && !currentSessionId) {
          setCurrentSessionId(codexActualSessionId);
          setIsSystemSessionChange(true);
          if (codexActualSessionId) {
            onNavigateToSession?.(codexActualSessionId);
          }
          sessionStorage.removeItem('pendingSessionId');
        }

        if (selectedProject) {
          safeLocalStorage.removeItem(`chat_messages_${selectedProject.name}`);
        }
        onWebSocketReconnect?.();
        scheduleSessionMetadataRefresh(onWebSocketReconnect);
        break;
      }

      case 'codex-error':
        finalizeLifecycleForCurrentView(latestMessage.sessionId, currentSessionId, selectedSession?.id);
        setChatMessages((previous) => [
          ...previous,
          {
            type: 'error',
            content: latestMessage.error || 'An error occurred with Codex',
            timestamp: new Date(),
          },
        ]);
        break;

      case 'session-aborted': {
        const pendingSessionId =
          typeof window !== 'undefined' ? sessionStorage.getItem('pendingSessionId') : null;
        const abortedSessionId = latestMessage.sessionId || currentSessionId;
        const abortSucceeded = latestMessage.success !== false;

        if (abortSucceeded) {
          finalizeLifecycleForCurrentView(abortedSessionId, currentSessionId, selectedSession?.id, pendingSessionId);
          if (pendingSessionId && (!abortedSessionId || pendingSessionId === abortedSessionId)) {
            sessionStorage.removeItem('pendingSessionId');
          }

          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content: 'Session interrupted by user.',
              timestamp: new Date(),
            },
          ]);
        } else {
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'error',
              content: 'Stop request failed. The session is still running.',
              timestamp: new Date(),
            },
          ]);
        }
        break;
      }

      case 'session-status': {
        const statusSessionId = latestMessage.sessionId;
        if (!statusSessionId) {
          break;
        }

        const isCurrentSession =
          statusSessionId === currentSessionId || (selectedSession && statusSessionId === selectedSession.id);

        if (latestMessage.isProcessing) {
          onSessionProcessing?.(statusSessionId);
          if (isCurrentSession) {
            setIsLoading(true);
            setCanAbortSession(true);
          }
          break;
        }

        onSessionInactive?.(statusSessionId);
        onSessionNotProcessing?.(statusSessionId);
        if (isCurrentSession) {
          clearLoadingIndicators();
        }
        break;
      }

      case 'claude-status': {
        const statusData = latestMessage.data;
        if (!statusData) {
          break;
        }

        const statusInfo: { text: string; tokens: number; can_interrupt: boolean } = {
          text: 'Working...',
          tokens: 0,
          can_interrupt: true,
        };

        if (statusData.message) {
          statusInfo.text = statusData.message;
        } else if (statusData.status) {
          statusInfo.text = statusData.status;
        } else if (typeof statusData === 'string') {
          statusInfo.text = statusData;
        }

        if (statusData.tokens) {
          statusInfo.tokens = statusData.tokens;
        } else if (statusData.token_count) {
          statusInfo.tokens = statusData.token_count;
        }

        if (statusData.can_interrupt !== undefined) {
          statusInfo.can_interrupt = statusData.can_interrupt;
        }

        setClaudeStatus(statusInfo);
        setIsLoading(true);
        setCanAbortSession(statusInfo.can_interrupt);
        break;
      }

      case 'pending-permissions-response': {
        // Server returned pending permissions for this session
        const permSessionId = latestMessage.sessionId;
        const isCurrentPermSession =
          permSessionId === currentSessionId || (selectedSession && permSessionId === selectedSession.id);
        if (permSessionId && !isCurrentPermSession) {
          break;
        }
        const serverRequests = latestMessage.data || [];
        setPendingPermissionRequests(serverRequests);
        break;
      }

      case 'error':
        // Generic backend failure (e.g., provider process failed before a provider-specific
        // completion event was emitted). Treat it as terminal for current view lifecycle.
        finalizeLifecycleForCurrentView(latestMessage.sessionId, currentSessionId, selectedSession?.id);
        break;

      default:
        break;
    }
  }, [
    latestMessage,
    provider,
    selectedProject,
    selectedSession,
    currentSessionId,
    setCurrentSessionId,
    setChatMessages,
    setIsLoading,
    setCanAbortSession,
    setClaudeStatus,
    setTokenBudget,
    setIsSystemSessionChange,
    setPendingPermissionRequests,
    onSessionInactive,
    onSessionProcessing,
    onSessionNotProcessing,
    onReplaceTemporarySession,
    onNavigateToSession,
  ]);
}
