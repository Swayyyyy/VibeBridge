import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  ChangeEvent,
  ClipboardEvent,
  Dispatch,
  FormEvent,
  KeyboardEvent,
  MouseEvent,
  SetStateAction,
  TouchEvent,
} from 'react';
import { useDropzone } from 'react-dropzone';
import type { ProviderThinkingEffort } from '../../../shared/providerSettings';
import { authenticatedFetch } from '../../../utils/api';
import { grantClaudeToolPermission } from '../utils/chatPermissions';
import { safeLocalStorage } from '../utils/chatStorage';
import type {
  ChatMessage,
  PendingPermissionRequest,
  PermissionMode,
  QueuedPromptItem,
  QueuedPromptMode,
} from '../types/types';
import type { Project, ProjectSession, SessionProvider } from '../../../types/app';
import { escapeRegExp } from '../utils/chatFormatting';
import { useFileMentions } from './useFileMentions';
import { type SlashCommand, useSlashCommands } from './useSlashCommands';

type PendingViewSession = {
  sessionId: string | null;
  startedAt: number;
};

interface UseChatComposerStateArgs {
  selectedProject: Project | null;
  selectedSession: ProjectSession | null;
  currentSessionId: string | null;
  provider: SessionProvider;
  permissionMode: PermissionMode | string;
  cyclePermissionMode: () => void;
  claudeModel: string;
  claudeThinkingEffort: ProviderThinkingEffort;
  codexModel: string;
  codexReasoningEffort: ProviderThinkingEffort;
  isLoading: boolean;
  canAbortSession: boolean;
  tokenBudget: Record<string, unknown> | null;
  sendMessage: (message: unknown) => void;
  sendByCtrlEnter?: boolean;
  onSessionActive?: (sessionId?: string | null) => void;
  onSessionProcessing?: (sessionId?: string | null) => void;
  onInputFocusChange?: (focused: boolean) => void;
  onFileOpen?: (filePath: string, diffInfo?: unknown) => void;
  onShowSettings?: () => void;
  pendingViewSessionRef: { current: PendingViewSession | null };
  scrollToBottom: () => void;
  setChatMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setSessionMessages?: Dispatch<SetStateAction<any[]>>;
  setIsLoading: (loading: boolean) => void;
  setCanAbortSession: (canAbort: boolean) => void;
  setClaudeStatus: (status: { text: string; tokens: number; can_interrupt: boolean } | null) => void;
  setIsUserScrolledUp: (isScrolledUp: boolean) => void;
  setPendingPermissionRequests: Dispatch<SetStateAction<PendingPermissionRequest[]>>;
}

interface MentionableFile {
  name: string;
  path: string;
}

interface CommandExecutionResult {
  type: 'builtin' | 'custom';
  action?: string;
  data?: any;
  content?: string;
  hasBashCommands?: boolean;
  hasFileIncludes?: boolean;
}

interface QueuedPrompt extends QueuedPromptItem {
  prompt: string;
  attachedImages: File[];
}

const createClientMessageId = () =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `message-${Date.now()}-${Math.random().toString(16).slice(2)}`;

const createFakeSubmitEvent = () => {
  return { preventDefault: () => undefined } as unknown as FormEvent<HTMLFormElement>;
};

const isTemporarySessionId = (sessionId: string | null | undefined) =>
  Boolean(
    sessionId &&
      (sessionId.startsWith('new-session-') || sessionId.startsWith('draft-')),
  );

const getDraftStorageKey = (selectedProject: Project) => {
  const nodeSegment = selectedProject.nodeId || 'local';
  return `draft_input_${nodeSegment}_${selectedProject.name}`;
};

export function useChatComposerState({
  selectedProject,
  selectedSession,
  currentSessionId,
  provider,
  permissionMode,
  cyclePermissionMode,
  claudeModel,
  claudeThinkingEffort,
  codexModel,
  codexReasoningEffort,
  isLoading,
  canAbortSession,
  tokenBudget,
  sendMessage,
  sendByCtrlEnter,
  onSessionActive,
  onSessionProcessing,
  onInputFocusChange,
  onFileOpen,
  onShowSettings,
  pendingViewSessionRef,
  scrollToBottom,
  setChatMessages,
  setSessionMessages,
  setIsLoading,
  setCanAbortSession,
  setClaudeStatus,
  setIsUserScrolledUp,
  setPendingPermissionRequests,
}: UseChatComposerStateArgs) {
  const [input, setInput] = useState(() => {
    if (typeof window !== 'undefined' && selectedProject) {
      return safeLocalStorage.getItem(getDraftStorageKey(selectedProject)) || '';
    }
    return '';
  });
  const [attachedImages, setAttachedImages] = useState<File[]>([]);
  const [uploadingImages, setUploadingImages] = useState<Map<string, number>>(new Map());
  const [imageErrors, setImageErrors] = useState<Map<string, string>>(new Map());
  const [isTextareaExpanded, setIsTextareaExpanded] = useState(false);
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const inputHighlightRef = useRef<HTMLDivElement>(null);
  const handleSubmitRef = useRef<
    ((event: FormEvent<HTMLFormElement> | MouseEvent | TouchEvent | KeyboardEvent<HTMLTextAreaElement>) => Promise<void>) | null
  >(null);
  const inputValueRef = useRef(input);
  const queuedPromptDispatchRef = useRef<string | null>(null);
  const supportsLiveTurnControl = provider === 'codex' || provider === 'claude';

  const queuePromptSubmission = useCallback((prompt: string) => {
    setInput(prompt);
    inputValueRef.current = prompt;

    // Defer submit to next tick so the injected prompt is reflected in UI.
    setTimeout(() => {
      if (handleSubmitRef.current) {
        handleSubmitRef.current(createFakeSubmitEvent());
      }
    }, 0);
  }, []);

  const resolveAbortTargetSessionId = useCallback(() => {
    const pendingSessionId =
      typeof window !== 'undefined' ? sessionStorage.getItem('pendingSessionId') : null;

    const candidateSessionIds = [
      currentSessionId,
      pendingViewSessionRef.current?.sessionId || null,
      pendingSessionId,
      selectedSession?.id || null,
    ];

    return (
      candidateSessionIds.find((sessionId) => Boolean(sessionId) && !isTemporarySessionId(sessionId)) || null
    );
  }, [currentSessionId, pendingViewSessionRef, selectedSession?.id]);

  const handleBuiltInCommand = useCallback(
    (result: CommandExecutionResult): boolean => {
      const { action, data } = result;
      switch (action) {
        case 'clear':
          setChatMessages([]);
          setSessionMessages?.([]);
          return true;

        case 'help':
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content: data.content,
              timestamp: Date.now(),
            },
          ]);
          return true;

        case 'model':
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content: `**Current Model**: ${data.current.model}\n\n**Available Models**:\n\nClaude: ${data.available.claude.join(', ')}\n\nCodex: ${(data.available.codex || []).join(', ')}`,
              timestamp: Date.now(),
            },
          ]);
          return true;

        case 'cost': {
          const costMessage = `**Token Usage**: ${data.tokenUsage.used.toLocaleString()} / ${data.tokenUsage.total.toLocaleString()} (${data.tokenUsage.percentage}%)\n\n**Estimated Cost**:\n- Input: $${data.cost.input}\n- Output: $${data.cost.output}\n- **Total**: $${data.cost.total}\n\n**Model**: ${data.model}`;
          setChatMessages((previous) => [
            ...previous,
            { type: 'assistant', content: costMessage, timestamp: Date.now() },
          ]);
          return true;
        }

        case 'status': {
          const statusMessage = `**System Status**\n\n- Version: ${data.version}\n- Uptime: ${data.uptime}\n- Model: ${data.model}\n- Provider: ${data.provider}\n- Node.js: ${data.nodeVersion}\n- Platform: ${data.platform}`;
          setChatMessages((previous) => [
            ...previous,
            { type: 'assistant', content: statusMessage, timestamp: Date.now() },
          ]);
          return true;
        }

        case 'memory':
          if (data.error) {
            setChatMessages((previous) => [
              ...previous,
              {
                type: 'assistant',
                content: `⚠️ ${data.message}`,
                timestamp: Date.now(),
              },
            ]);
          } else {
            setChatMessages((previous) => [
              ...previous,
              {
                type: 'assistant',
                content: `📝 ${data.message}\n\nPath: \`${data.path}\``,
                timestamp: Date.now(),
              },
            ]);
            if (data.exists && onFileOpen) {
              onFileOpen(data.path);
            }
          }
          return true;

        case 'permissions':
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content: `🔐 ${data.message}`,
              timestamp: Date.now(),
            },
          ]);
          if (typeof window !== 'undefined' && typeof window.openSettings === 'function') {
            window.openSettings('agents');
          } else {
            onShowSettings?.();
          }
          return true;

        case 'review':
          if (typeof data?.prompt === 'string' && data.prompt.trim()) {
            queuePromptSubmission(data.prompt.trim());
            return false;
          }
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'assistant',
              content: 'Unable to start review because no prompt was returned.',
              timestamp: Date.now(),
            },
          ]);
          return true;

        case 'config':
          if (typeof window !== 'undefined' && typeof window.openSettings === 'function') {
            window.openSettings('agents');
          } else {
            onShowSettings?.();
          }
          return true;

        case 'rewind':
          if (data.error) {
            setChatMessages((previous) => [
              ...previous,
              {
                type: 'assistant',
                content: `⚠️ ${data.message}`,
                timestamp: Date.now(),
              },
            ]);
          } else {
            setChatMessages((previous) => previous.slice(0, -data.steps * 2));
            setChatMessages((previous) => [
              ...previous,
              {
                type: 'assistant',
                content: `⏪ ${data.message}`,
                timestamp: Date.now(),
              },
            ]);
          }
          return true;

        default:
          console.warn('Unknown built-in command action:', action);
          return true;
      }
    },
    [onFileOpen, onShowSettings, queuePromptSubmission, setChatMessages, setSessionMessages],
  );

  const handleCustomCommand = useCallback(async (result: CommandExecutionResult) => {
    const { content, hasBashCommands } = result;

    if (hasBashCommands) {
      const confirmed = window.confirm(
        'This command contains bash commands that will be executed. Do you want to proceed?',
      );
      if (!confirmed) {
        setChatMessages((previous) => [
          ...previous,
          {
            type: 'assistant',
            content: '❌ Command execution cancelled',
            timestamp: Date.now(),
          },
        ]);
        return;
      }
    }

    const commandContent = content || '';
    queuePromptSubmission(commandContent);
  }, [queuePromptSubmission, setChatMessages]);

  const executeCommand = useCallback(
    async (command: SlashCommand, rawInput?: string): Promise<boolean> => {
      if (!command || !selectedProject) {
        return true;
      }

      try {
        const effectiveInput = rawInput ?? input;
        const commandMatch = effectiveInput.match(new RegExp(`${escapeRegExp(command.name)}\\s*(.*)`));
        const args =
          commandMatch && commandMatch[1] ? commandMatch[1].trim().split(/\s+/) : [];

        const context = {
          projectPath: selectedProject.fullPath || selectedProject.path,
          projectName: selectedProject.name,
          sessionId: currentSessionId,
          provider,
          model: provider === 'codex' ? codexModel : claudeModel,
          tokenUsage: tokenBudget,
        };

        const response = await authenticatedFetch('/api/commands/execute', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            commandName: command.name,
            commandPath: command.path,
            provider,
            args,
            context,
          }),
        });

        if (!response.ok) {
          let errorMessage = `Failed to execute command (${response.status})`;
          try {
            const errorData = await response.json();
            errorMessage = errorData?.message || errorData?.error || errorMessage;
          } catch {
            // Ignore JSON parse failures and use fallback message.
          }
          throw new Error(errorMessage);
        }

        const result = (await response.json()) as CommandExecutionResult;
        if (result.type === 'builtin') {
          return handleBuiltInCommand(result);
        } else if (result.type === 'custom') {
          await handleCustomCommand(result);
          return false;
        }
        return true;
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unknown error';
        console.error('Error executing command:', error);
        setChatMessages((previous) => [
          ...previous,
          {
            type: 'assistant',
            content: `Error executing command: ${message}`,
            timestamp: Date.now(),
          },
        ]);
        return true;
      }
    },
    [
      claudeModel,
      codexModel,
      currentSessionId,
      handleBuiltInCommand,
      handleCustomCommand,
      input,
      provider,
      selectedProject,
      setChatMessages,
      tokenBudget,
    ],
  );

  const {
    slashCommands,
    slashCommandsCount,
    filteredCommands,
    frequentCommands,
    commandQuery,
    showCommandMenu,
    selectedCommandIndex,
    resetCommandMenuState,
    handleCommandSelect,
    handleToggleCommandMenu,
    handleCommandInputChange,
    handleCommandMenuKeyDown,
  } = useSlashCommands({
    selectedProject,
    provider,
    input,
    setInput,
    textareaRef,
    onExecuteCommand: executeCommand,
  });

  const {
    showFileDropdown,
    filteredFiles,
    selectedFileIndex,
    renderInputWithMentions,
    selectFile,
    setCursorPosition,
    handleFileMentionsKeyDown,
  } = useFileMentions({
    selectedProject,
    input,
    setInput,
    textareaRef,
  });

  const clearComposerState = useCallback(() => {
    setInput('');
    inputValueRef.current = '';
    resetCommandMenuState();
    setAttachedImages([]);
    setUploadingImages(new Map());
    setImageErrors(new Map());
    setIsTextareaExpanded(false);

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    if (selectedProject) {
      safeLocalStorage.removeItem(getDraftStorageKey(selectedProject));
    }
  }, [resetCommandMenuState, selectedProject]);

  const syncInputOverlayScroll = useCallback((target: HTMLTextAreaElement) => {
    if (!inputHighlightRef.current || !target) {
      return;
    }
    inputHighlightRef.current.scrollTop = target.scrollTop;
    inputHighlightRef.current.scrollLeft = target.scrollLeft;
  }, []);

  const handleImageFiles = useCallback((files: File[]) => {
    const validFiles = files.filter((file) => {
      try {
        if (!file || typeof file !== 'object') {
          console.warn('Invalid file object:', file);
          return false;
        }

        if (!file.type || !file.type.startsWith('image/')) {
          return false;
        }

        if (!file.size || file.size > 5 * 1024 * 1024) {
          const fileName = file.name || 'Unknown file';
          setImageErrors((previous) => {
            const next = new Map(previous);
            next.set(fileName, 'File too large (max 5MB)');
            return next;
          });
          return false;
        }

        return true;
      } catch (error) {
        console.error('Error validating file:', error, file);
        return false;
      }
    });

    if (validFiles.length > 0) {
      setAttachedImages((previous) => [...previous, ...validFiles].slice(0, 5));
    }
  }, []);

  const handlePaste = useCallback(
    (event: ClipboardEvent<HTMLTextAreaElement>) => {
      const items = Array.from(event.clipboardData.items);

      items.forEach((item) => {
        if (!item.type.startsWith('image/')) {
          return;
        }
        const file = item.getAsFile();
        if (file) {
          handleImageFiles([file]);
        }
      });

      if (items.length === 0 && event.clipboardData.files.length > 0) {
        const files = Array.from(event.clipboardData.files);
        const imageFiles = files.filter((file) => file.type.startsWith('image/'));
        if (imageFiles.length > 0) {
          handleImageFiles(imageFiles);
        }
      }
    },
    [handleImageFiles],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    accept: {
      'image/*': ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'],
    },
    maxSize: 5 * 1024 * 1024,
    maxFiles: 5,
    onDrop: handleImageFiles,
    noClick: true,
    noKeyboard: true,
  });

  const submitPrompt = useCallback(
    async (
      promptText: string,
      promptImages: File[],
      options: { allowWhileLoading?: boolean; skipUserMessageAppend?: boolean } = {},
    ): Promise<boolean> => {
      if (!promptText.trim() || ((!options.allowWhileLoading && isLoading) || !selectedProject)) {
        return false;
      }

      const trimmedInput = promptText.trim();
      if (trimmedInput.startsWith('/')) {
        const firstSpace = trimmedInput.indexOf(' ');
        const commandName = firstSpace > 0 ? trimmedInput.slice(0, firstSpace) : trimmedInput;
        const matchedCommand = slashCommands.find((cmd: SlashCommand) => cmd.name === commandName);
        if (matchedCommand) {
          const shouldClearComposer = await executeCommand(matchedCommand, trimmedInput);
          if (shouldClearComposer) {
            clearComposerState();
          }
          return true;
        }
      }

      let uploadedImages: unknown[] = [];
      if (promptImages.length > 0) {
        const formData = new FormData();
        promptImages.forEach((file) => {
          formData.append('images', file);
        });

        try {
          const response = await authenticatedFetch(`/api/projects/${selectedProject.name}/upload-images`, {
            method: 'POST',
            headers: {},
            body: formData,
          });

          if (!response.ok) {
            throw new Error('Failed to upload images');
          }

          const result = await response.json();
          uploadedImages = result.images;
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Unknown error';
          console.error('Image upload failed:', error);
          setChatMessages((previous) => [
            ...previous,
            {
              type: 'error',
              content: `Failed to upload images: ${message}`,
              timestamp: new Date(),
            },
          ]);
          return false;
        }
      }

      if (!options.skipUserMessageAppend) {
        const messageId = createClientMessageId();
        const userMessage: ChatMessage = {
          id: messageId,
          messageId,
          type: 'user',
          content: promptText,
          images: uploadedImages as any,
          timestamp: new Date(),
        };

        setChatMessages((previous) => [...previous, userMessage]);
      }
      setIsLoading(true);
      setCanAbortSession(true);
      setClaudeStatus({
        text: 'Processing',
        tokens: 0,
        can_interrupt: true,
      });

      setIsUserScrolledUp(false);
      setTimeout(() => scrollToBottom(), 100);

      const effectiveSessionId = currentSessionId || selectedSession?.id;
      const sessionToActivate = effectiveSessionId || `new-session-${Date.now()}`;

      if (!effectiveSessionId && !selectedSession?.id) {
        if (typeof window !== 'undefined') {
          sessionStorage.removeItem('pendingSessionId');
        }
        pendingViewSessionRef.current = { sessionId: null, startedAt: Date.now() };
      }
      onSessionActive?.(sessionToActivate);
      if (effectiveSessionId && !isTemporarySessionId(effectiveSessionId)) {
        onSessionProcessing?.(effectiveSessionId);
      }

      const getToolsSettings = () => {
        try {
          const settingsKey =
            provider === 'codex' ? 'codex-settings' : 'claude-settings';
          const savedSettings = safeLocalStorage.getItem(settingsKey);
          if (savedSettings) {
            return JSON.parse(savedSettings);
          }
        } catch (error) {
          console.error('Error loading tools settings:', error);
        }

        return {
          allowedTools: [],
          disallowedTools: [],
          skipPermissions: false,
        };
      };

      const toolsSettings = getToolsSettings();
      const resolvedProjectPath = selectedProject.fullPath || selectedProject.path || '';

      if (provider === 'codex') {
        const codexReasoningMap: Record<ProviderThinkingEffort, string> = {
          low: 'low',
          medium: 'medium',
          high: 'high',
          'ultra-high': 'xhigh',
        };
        sendMessage({
          type: 'codex-command',
          command: promptText,
          sessionId: effectiveSessionId,
          options: {
            cwd: resolvedProjectPath,
            projectPath: resolvedProjectPath,
            sessionId: effectiveSessionId,
            resume: Boolean(effectiveSessionId),
            model: codexModel,
            permissionMode: permissionMode === 'plan' ? 'default' : permissionMode,
            reasoningEffort: codexReasoningMap[codexReasoningEffort] || 'medium',
          },
        });
      } else {
        const claudeEffortMap: Record<ProviderThinkingEffort, string> = {
          low: 'low',
          medium: 'medium',
          high: 'high',
          'ultra-high': 'max',
        };
        sendMessage({
          type: 'claude-command',
          command: promptText,
          options: {
            projectPath: resolvedProjectPath,
            cwd: resolvedProjectPath,
            sessionId: effectiveSessionId,
            resume: Boolean(effectiveSessionId),
            toolsSettings,
            permissionMode,
            model: claudeModel,
            thinkingEffort: claudeEffortMap[claudeThinkingEffort] || 'medium',
            images: uploadedImages,
          },
        });
      }

      clearComposerState();
      return true;
    },
    [
      claudeModel,
      claudeThinkingEffort,
      clearComposerState,
      codexModel,
      codexReasoningEffort,
      currentSessionId,
      executeCommand,
      isLoading,
      onSessionActive,
      onSessionProcessing,
      pendingViewSessionRef,
      permissionMode,
      provider,
      scrollToBottom,
      selectedProject,
      selectedSession?.id,
      sendMessage,
      setCanAbortSession,
      setChatMessages,
      setClaudeStatus,
      setIsLoading,
      setIsUserScrolledUp,
      slashCommands,
    ],
  );

  const enqueueCurrentLivePrompt = useCallback(
    (mode: QueuedPromptMode) => {
      if (!supportsLiveTurnControl || !selectedProject) {
        return false;
      }

      const promptText = inputValueRef.current.trim();
      if (!promptText) {
        return false;
      }

      const targetSessionId =
        currentSessionId || pendingViewSessionRef.current?.sessionId || selectedSession?.id || null;
      const queueItem: QueuedPrompt = {
        id:
          typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
            ? crypto.randomUUID()
            : `queued-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        prompt: promptText,
        attachedImages: [...attachedImages],
        provider,
        projectName: selectedProject.name,
        nodeId: selectedProject.nodeId || null,
        targetSessionId,
        mode,
        state: 'queued',
        queuedAt: Date.now(),
      };

      setQueuedPrompts((previous) => (mode === 'guide' ? [queueItem, ...previous] : [...previous, queueItem]));
      clearComposerState();
      setTimeout(() => scrollToBottom(), 100);

      if (mode === 'guide') {
        const abortTarget = resolveAbortTargetSessionId();
        if (abortTarget && canAbortSession) {
          sendMessage({
            type: 'abort-session',
            sessionId: abortTarget,
            provider,
          });
        }
      }

      return true;
    },
    [
      attachedImages,
      canAbortSession,
      clearComposerState,
      currentSessionId,
      pendingViewSessionRef,
      provider,
      resolveAbortTargetSessionId,
      scrollToBottom,
      selectedProject,
      selectedSession?.id,
      sendMessage,
      supportsLiveTurnControl,
    ],
  );

  const matchesQueuedPromptContext = useCallback(
    (queueItem: QueuedPrompt) => {
      if (!selectedProject) {
        return false;
      }

      const currentNodeId = selectedProject.nodeId || null;
      if (
        queueItem.provider !== provider ||
        queueItem.projectName !== selectedProject.name ||
        queueItem.nodeId !== currentNodeId
      ) {
        return false;
      }

      const pendingSessionId =
        typeof window !== 'undefined' ? sessionStorage.getItem('pendingSessionId') : null;
      const candidateSessionIds = [
        currentSessionId,
        pendingViewSessionRef.current?.sessionId || null,
        pendingSessionId,
        selectedSession?.id || null,
      ].filter((sessionId): sessionId is string => Boolean(sessionId));

      const stableTargetSessionId =
        queueItem.targetSessionId && !isTemporarySessionId(queueItem.targetSessionId)
          ? queueItem.targetSessionId
          : null;

      if (stableTargetSessionId) {
        return candidateSessionIds.includes(stableTargetSessionId);
      }

      return candidateSessionIds.length === 0 || candidateSessionIds.some((sessionId) => isTemporarySessionId(sessionId));
    },
    [currentSessionId, pendingViewSessionRef, provider, selectedProject, selectedSession?.id],
  );

  useEffect(() => {
    if (!selectedProject) {
      return;
    }

    const stableSessionId = [currentSessionId, selectedSession?.id || null].find(
      (sessionId): sessionId is string => Boolean(sessionId) && !isTemporarySessionId(sessionId),
    );
    if (!stableSessionId) {
      return;
    }

    const currentNodeId = selectedProject.nodeId || null;
    setQueuedPrompts((previous) => {
      let changed = false;
      const next = previous.map((queueItem) => {
        if (
          queueItem.provider !== provider ||
          queueItem.projectName !== selectedProject.name ||
          queueItem.nodeId !== currentNodeId
        ) {
          return queueItem;
        }

        if (queueItem.targetSessionId && !isTemporarySessionId(queueItem.targetSessionId)) {
          return queueItem;
        }

        changed = true;
        return { ...queueItem, targetSessionId: stableSessionId };
      });

      return changed ? next : previous;
    });
  }, [currentSessionId, provider, selectedProject, selectedSession?.id]);

  const dispatchQueuedPrompt = useCallback(async () => {
    if (isLoading || queuedPromptDispatchRef.current) {
      return;
    }

    const nextQueuedPrompt = queuedPrompts.find(
      (queueItem) => queueItem.state === 'queued' && matchesQueuedPromptContext(queueItem),
    );
    if (!nextQueuedPrompt) {
      return;
    }

    queuedPromptDispatchRef.current = nextQueuedPrompt.id;
    setQueuedPrompts((previous) =>
      previous.map((queueItem) =>
        queueItem.id === nextQueuedPrompt.id ? { ...queueItem, state: 'dispatching' } : queueItem,
      ),
    );

    try {
      const submitted = await submitPrompt(nextQueuedPrompt.prompt, nextQueuedPrompt.attachedImages, {
        allowWhileLoading: true,
      });
      if (submitted) {
        setQueuedPrompts((previous) => previous.filter((queueItem) => queueItem.id !== nextQueuedPrompt.id));
      } else {
        setQueuedPrompts((previous) =>
          previous.map((queueItem) =>
            queueItem.id === nextQueuedPrompt.id ? { ...queueItem, state: 'queued' } : queueItem,
          ),
        );
      }
    } finally {
      queuedPromptDispatchRef.current = null;
    }
  }, [isLoading, matchesQueuedPromptContext, queuedPrompts, setChatMessages, submitPrompt]);

  useEffect(() => {
    void dispatchQueuedPrompt();
  }, [dispatchQueuedPrompt]);

  const handleGuideQueuedPrompt = useCallback(
    (queuePromptId: string) => {
      const targetPrompt = queuedPrompts.find(
        (queueItem) => queueItem.id === queuePromptId && queueItem.state === 'queued',
      );
      if (!targetPrompt) {
        return;
      }

      setQueuedPrompts((previous) => {
        const prioritizedPrompt = { ...targetPrompt, mode: 'guide' as const };
        return [
          prioritizedPrompt,
          ...previous.filter((queueItem) => queueItem.id !== queuePromptId),
        ];
      });

      if (!isLoading || !canAbortSession) {
        return;
      }

      const abortTarget = resolveAbortTargetSessionId();
      if (!abortTarget) {
        return;
      }

      sendMessage({
        type: 'abort-session',
        sessionId: abortTarget,
        provider,
      });
    },
    [canAbortSession, isLoading, provider, queuedPrompts, resolveAbortTargetSessionId, sendMessage],
  );

  const handleCancelQueuedPrompt = useCallback(
    (queuePromptId: string) => {
      setQueuedPrompts((previous) => previous.filter((queueItem) => queueItem.id !== queuePromptId));
    },
    [],
  );

  const handleEditQueuedPrompt = useCallback(
    (queuePromptId: string) => {
      const queueItemToEdit = queuedPrompts.find(
        (queueItem) => queueItem.id === queuePromptId && queueItem.state === 'queued',
      );

      if (!queueItemToEdit) {
        return;
      }

      setQueuedPrompts((previous) => previous.filter((queueItem) => queueItem.id !== queuePromptId));
      setInput(queueItemToEdit.prompt);
      inputValueRef.current = queueItemToEdit.prompt;
      setAttachedImages([...queueItemToEdit.attachedImages]);

      setTimeout(() => {
        textareaRef.current?.focus();
        scrollToBottom();
      }, 0);
    },
    [queuedPrompts, scrollToBottom, textareaRef],
  );

  const handleSubmit = useCallback(
    async (
      event: FormEvent<HTMLFormElement> | MouseEvent | TouchEvent | KeyboardEvent<HTMLTextAreaElement>,
    ) => {
      event.preventDefault();

      if (supportsLiveTurnControl && isLoading) {
        enqueueCurrentLivePrompt('queue');
        return;
      }

      await submitPrompt(inputValueRef.current, attachedImages);
    },
    [attachedImages, enqueueCurrentLivePrompt, isLoading, submitPrompt, supportsLiveTurnControl],
  );

  useEffect(() => {
    handleSubmitRef.current = handleSubmit;
  }, [handleSubmit]);

  useEffect(() => {
    inputValueRef.current = input;
  }, [input]);

  useEffect(() => {
    if (!selectedProject) {
      return;
    }
    const savedInput = safeLocalStorage.getItem(getDraftStorageKey(selectedProject)) || '';
    setInput((previous) => {
      const next = previous === savedInput ? previous : savedInput;
      inputValueRef.current = next;
      return next;
    });
  }, [selectedProject?.name]);

  useEffect(() => {
    if (!selectedProject) {
      return;
    }
    if (input !== '') {
      safeLocalStorage.setItem(getDraftStorageKey(selectedProject), input);
    } else {
      safeLocalStorage.removeItem(getDraftStorageKey(selectedProject));
    }
  }, [input, selectedProject]);

  useEffect(() => {
    if (!textareaRef.current) {
      return;
    }
    // Re-run when input changes so restored drafts get the same autosize behavior as typed text.
    textareaRef.current.style.height = 'auto';
    textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    const lineHeight = parseInt(window.getComputedStyle(textareaRef.current).lineHeight);
    const expanded = textareaRef.current.scrollHeight > lineHeight * 2;
    setIsTextareaExpanded(expanded);
  }, [input]);

  useEffect(() => {
    if (!textareaRef.current || input.trim()) {
      return;
    }
    textareaRef.current.style.height = 'auto';
    setIsTextareaExpanded(false);
  }, [input]);

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = event.target.value;
      const cursorPos = event.target.selectionStart;

      setInput(newValue);
      inputValueRef.current = newValue;
      setCursorPosition(cursorPos);

      if (!newValue.trim()) {
        event.target.style.height = 'auto';
        setIsTextareaExpanded(false);
        resetCommandMenuState();
        return;
      }

      handleCommandInputChange(newValue, cursorPos);
    },
    [handleCommandInputChange, resetCommandMenuState, setCursorPosition],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (handleCommandMenuKeyDown(event)) {
        return;
      }

      if (handleFileMentionsKeyDown(event)) {
        return;
      }

      if (event.key === 'Tab' && !showFileDropdown && !showCommandMenu) {
        event.preventDefault();
        cyclePermissionMode();
        return;
      }

      if (event.key === 'Enter') {
        if (event.nativeEvent.isComposing) {
          return;
        }

        if ((event.ctrlKey || event.metaKey) && !event.shiftKey) {
          event.preventDefault();
          handleSubmit(event);
        } else if (!event.shiftKey && !event.ctrlKey && !event.metaKey && !sendByCtrlEnter) {
          event.preventDefault();
          handleSubmit(event);
        }
      }
    },
    [
      cyclePermissionMode,
      handleCommandMenuKeyDown,
      handleFileMentionsKeyDown,
      handleSubmit,
      sendByCtrlEnter,
      showCommandMenu,
      showFileDropdown,
    ],
  );

  const handleTextareaClick = useCallback(
    (event: MouseEvent<HTMLTextAreaElement>) => {
      setCursorPosition(event.currentTarget.selectionStart);
    },
    [setCursorPosition],
  );

  const handleTextareaInput = useCallback(
    (event: FormEvent<HTMLTextAreaElement>) => {
      const target = event.currentTarget;
      target.style.height = 'auto';
      target.style.height = `${target.scrollHeight}px`;
      setCursorPosition(target.selectionStart);
      syncInputOverlayScroll(target);

      const lineHeight = parseInt(window.getComputedStyle(target).lineHeight);
      setIsTextareaExpanded(target.scrollHeight > lineHeight * 2);
    },
    [setCursorPosition, syncInputOverlayScroll],
  );

  const handleClearInput = useCallback(() => {
    setInput('');
    inputValueRef.current = '';
    resetCommandMenuState();
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.focus();
    }
    setIsTextareaExpanded(false);
  }, [resetCommandMenuState]);

  const handleAbortSession = useCallback(() => {
    if (!canAbortSession) {
      return;
    }
    const targetSessionId = resolveAbortTargetSessionId();

    if (!targetSessionId) {
      console.warn('Abort requested but no concrete session ID is available yet.');
      return;
    }

    sendMessage({
      type: 'abort-session',
      sessionId: targetSessionId,
      provider,
    });
  }, [canAbortSession, provider, resolveAbortTargetSessionId, sendMessage]);

  const handleTranscript = useCallback((text: string) => {
    if (!text.trim()) {
      return;
    }

    setInput((previousInput) => {
      const newInput = previousInput.trim() ? `${previousInput} ${text}` : text;
      inputValueRef.current = newInput;

      setTimeout(() => {
        if (!textareaRef.current) {
          return;
        }

        textareaRef.current.style.height = 'auto';
        textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
        const lineHeight = parseInt(window.getComputedStyle(textareaRef.current).lineHeight);
        setIsTextareaExpanded(textareaRef.current.scrollHeight > lineHeight * 2);
      }, 0);

      return newInput;
    });
  }, []);

  const handleGrantToolPermission = useCallback(
    (suggestion: { entry: string; toolName: string }) => {
      if (!suggestion || provider !== 'claude') {
        return { success: false };
      }
      return grantClaudeToolPermission(suggestion.entry);
    },
    [provider],
  );

  const handlePermissionDecision = useCallback(
    (
      requestIds: string | string[],
      decision: { allow?: boolean; message?: string; rememberEntry?: string | null; updatedInput?: unknown },
    ) => {
      const ids = Array.isArray(requestIds) ? requestIds : [requestIds];
      const validIds = ids.filter(Boolean);
      if (validIds.length === 0) {
        return;
      }

      validIds.forEach((requestId) => {
        sendMessage({
          type: provider === 'codex' ? 'codex-permission-response' : 'claude-permission-response',
          provider,
          requestId,
          allow: Boolean(decision?.allow),
          updatedInput: decision?.updatedInput,
          message: decision?.message,
          rememberEntry: decision?.rememberEntry,
        });
      });

      setPendingPermissionRequests((previous) => {
        const next = previous.filter((request) => !validIds.includes(request.requestId));
        if (next.length === 0) {
          setClaudeStatus(null);
        }
        return next;
      });
    },
    [provider, sendMessage, setClaudeStatus, setPendingPermissionRequests],
  );

  const [isInputFocused, setIsInputFocused] = useState(false);
  const visibleQueuedPrompts = queuedPrompts.filter(
    (queueItem) => queueItem.state === 'queued' && matchesQueuedPromptContext(queueItem),
  );

  const handleInputFocusChange = useCallback(
    (focused: boolean) => {
      setIsInputFocused(focused);
      onInputFocusChange?.(focused);
    },
    [onInputFocusChange],
  );

  return {
    input,
    setInput,
    textareaRef,
    inputHighlightRef,
    isTextareaExpanded,
    slashCommandsCount,
    filteredCommands,
    frequentCommands,
    commandQuery,
    showCommandMenu,
    selectedCommandIndex,
    resetCommandMenuState,
    handleCommandSelect,
    handleToggleCommandMenu,
    showFileDropdown,
    filteredFiles: filteredFiles as MentionableFile[],
    selectedFileIndex,
    renderInputWithMentions,
    selectFile,
    attachedImages,
    setAttachedImages,
    uploadingImages,
    imageErrors,
    getRootProps,
    getInputProps,
    isDragActive,
    openImagePicker: open,
    visibleQueuedPrompts,
    handleSubmit,
    handleGuideQueuedPrompt,
    handleCancelQueuedPrompt,
    handleEditQueuedPrompt,
    handleInputChange,
    handleKeyDown,
    handlePaste,
    handleTextareaClick,
    handleTextareaInput,
    syncInputOverlayScroll,
    handleClearInput,
    handleAbortSession,
    handleTranscript,
    handlePermissionDecision,
    handleGrantToolPermission,
    handleInputFocusChange,
    isInputFocused,
  };
}
