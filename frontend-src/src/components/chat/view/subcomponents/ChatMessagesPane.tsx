import { useTranslation } from 'react-i18next';
import { useCallback, useRef } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import type { ProviderThinkingEffort } from '../../../../shared/providerSettings';
import type { ChatMessage } from '../../types/types';
import type { Project, ProjectSession, SessionProvider } from '../../../../types/app';
import { getIntrinsicMessageKey } from '../../utils/messageKeys';
import MessageComponent from './MessageComponent';
import ProviderSelectionEmptyState from './ProviderSelectionEmptyState';
import AssistantThinkingIndicator from './AssistantThinkingIndicator';
import ToolCallBatch from './ToolCallBatch';

interface ChatMessagesPaneProps {
  scrollContainerRef: RefObject<HTMLDivElement>;
  onWheel: () => void;
  onTouchMove: () => void;
  isLoadingSessionMessages: boolean;
  chatMessages: ChatMessage[];
  selectedSession: ProjectSession | null;
  currentSessionId: string | null;
  provider: SessionProvider;
  setProvider: (provider: SessionProvider) => void;
  textareaRef: RefObject<HTMLTextAreaElement>;
  claudeModel: string;
  setClaudeModel: (model: string) => void;
  claudeThinkingEffort: ProviderThinkingEffort;
  setClaudeThinkingEffort: (effort: ProviderThinkingEffort) => void;
  codexModel: string;
  setCodexModel: (model: string) => void;
  codexReasoningEffort: ProviderThinkingEffort;
  setCodexReasoningEffort: (effort: ProviderThinkingEffort) => void;
  tasksEnabled: boolean;
  isTaskMasterInstalled: boolean | null;
  onShowAllTasks?: (() => void) | null;
  setInput: Dispatch<SetStateAction<string>>;
  isLoadingMoreMessages: boolean;
  hasMoreMessages: boolean;
  totalMessages: number;
  sessionMessagesCount: number;
  visibleMessageCount: number;
  visibleMessages: ChatMessage[];
  loadEarlierMessages: () => void;
  loadAllMessages: () => void;
  allMessagesLoaded: boolean;
  isLoadingAllMessages: boolean;
  loadAllJustFinished: boolean;
  showLoadAllOverlay: boolean;
  createDiff: any;
  onFileOpen?: (filePath: string, diffInfo?: unknown) => void;
  onShowSettings?: () => void;
  onGrantToolPermission: (suggestion: { entry: string; toolName: string }) => { success: boolean };
  autoExpandTools?: boolean;
  showRawParameters?: boolean;
  showThinking?: boolean;
  selectedProject: Project;
  isLoading: boolean;
}

type StandaloneMessageGroup = {
  kind: 'standalone';
  messages: ChatMessage[];
  startIndex: number;
};

type TurnMessageGroup = {
  kind: 'turn';
  userMessage: ChatMessage;
  userIndex: number;
  leadingProcessMessages: ChatMessage[];
  finalReply: ChatMessage | null;
  trailingProcessMessages: ChatMessage[];
  trailingVisibleMessage: ChatMessage | null;
};

type MessageRenderGroup = StandaloneMessageGroup | TurnMessageGroup;

const isFormalAssistantReply = (message: ChatMessage) =>
  message.type === 'assistant' &&
  !message.isThinking &&
  !message.isToolUse &&
  !message.isInteractivePrompt &&
  !message.isTaskNotification &&
  !message.isCompactionStatus;

const shouldKeepVisibleWithoutFinalReply = (message: ChatMessage | null) =>
  Boolean(message && (message.type === 'error' || message.isInteractivePrompt));

const isVisibleProcessMessage = (message: ChatMessage, showThinking?: boolean) => {
  if (message.isThinking && !showThinking) {
    return false;
  }

  return true;
};

const groupMessagesIntoTurns = (messages: ChatMessage[]): MessageRenderGroup[] => {
  const groups: MessageRenderGroup[] = [];
  let standaloneBuffer: ChatMessage[] = [];
  let standaloneStartIndex = 0;

  const flushStandaloneBuffer = () => {
    if (standaloneBuffer.length === 0) {
      return;
    }

    groups.push({
      kind: 'standalone',
      messages: [...standaloneBuffer],
      startIndex: standaloneStartIndex,
    });
    standaloneBuffer = [];
  };

  let index = 0;
  while (index < messages.length) {
    const message = messages[index];

    if (message.type !== 'user') {
      if (standaloneBuffer.length === 0) {
        standaloneStartIndex = index;
      }
      standaloneBuffer.push(message);
      index += 1;
      continue;
    }

    flushStandaloneBuffer();

    let nextUserIndex = index + 1;
    while (nextUserIndex < messages.length && messages[nextUserIndex].type !== 'user') {
      nextUserIndex += 1;
    }

    const roundMessages = messages.slice(index + 1, nextUserIndex);
    let finalReplyIndex = -1;
    for (let roundIndex = roundMessages.length - 1; roundIndex >= 0; roundIndex -= 1) {
      if (isFormalAssistantReply(roundMessages[roundIndex])) {
        finalReplyIndex = roundIndex;
        break;
      }
    }

    let leadingProcessMessages = roundMessages;
    let finalReply: ChatMessage | null = null;
    let trailingProcessMessages: ChatMessage[] = [];
    let trailingVisibleMessage: ChatMessage | null = null;

    if (finalReplyIndex >= 0) {
      finalReply = roundMessages[finalReplyIndex];
      leadingProcessMessages = roundMessages.slice(0, finalReplyIndex);
      trailingProcessMessages = roundMessages.slice(finalReplyIndex + 1);
    } else {
      const lastRoundMessage = roundMessages[roundMessages.length - 1] ?? null;
      if (shouldKeepVisibleWithoutFinalReply(lastRoundMessage)) {
        trailingVisibleMessage = lastRoundMessage;
        leadingProcessMessages = roundMessages.slice(0, -1);
      }
    }

    groups.push({
      kind: 'turn',
      userMessage: message,
      userIndex: index,
      leadingProcessMessages,
      finalReply,
      trailingProcessMessages,
      trailingVisibleMessage,
    });
    index = nextUserIndex;
  }

  flushStandaloneBuffer();
  return groups;
};

export default function ChatMessagesPane({
  scrollContainerRef,
  onWheel,
  onTouchMove,
  isLoadingSessionMessages,
  chatMessages,
  selectedSession,
  currentSessionId,
  provider,
  setProvider,
  textareaRef,
  claudeModel,
  setClaudeModel,
  claudeThinkingEffort,
  setClaudeThinkingEffort,
  codexModel,
  setCodexModel,
  codexReasoningEffort,
  setCodexReasoningEffort,
  tasksEnabled,
  isTaskMasterInstalled,
  onShowAllTasks,
  setInput,
  isLoadingMoreMessages,
  hasMoreMessages,
  totalMessages,
  sessionMessagesCount,
  visibleMessageCount,
  visibleMessages,
  loadEarlierMessages,
  loadAllMessages,
  allMessagesLoaded,
  isLoadingAllMessages,
  loadAllJustFinished,
  showLoadAllOverlay,
  createDiff,
  onFileOpen,
  onShowSettings,
  onGrantToolPermission,
  autoExpandTools,
  showRawParameters,
  showThinking,
  selectedProject,
  isLoading,
}: ChatMessagesPaneProps) {
  const { t } = useTranslation('chat');
  const messageKeyMapRef = useRef<WeakMap<ChatMessage, string>>(new WeakMap());
  const allocatedKeysRef = useRef<Set<string>>(new Set());
  const generatedMessageKeyCounterRef = useRef(0);
  const groupedMessages = groupMessagesIntoTurns(visibleMessages);

  // Keep keys stable across prepends so existing MessageComponent instances retain local state.
  const getMessageKey = useCallback((message: ChatMessage) => {
    const existingKey = messageKeyMapRef.current.get(message);
    if (existingKey) {
      return existingKey;
    }

    const intrinsicKey = getIntrinsicMessageKey(message);
    let candidateKey = intrinsicKey;

    if (!candidateKey || allocatedKeysRef.current.has(candidateKey)) {
      do {
        generatedMessageKeyCounterRef.current += 1;
        candidateKey = intrinsicKey
          ? `${intrinsicKey}-${generatedMessageKeyCounterRef.current}`
          : `message-generated-${generatedMessageKeyCounterRef.current}`;
      } while (allocatedKeysRef.current.has(candidateKey));
    }

    allocatedKeysRef.current.add(candidateKey);
    messageKeyMapRef.current.set(message, candidateKey);
    return candidateKey;
  }, []);

  let latestUserMessageIndex = -1;
  visibleMessages.forEach((message, index) => {
    if (message.type === 'user') {
      latestUserMessageIndex = index;
    }
  });

  const renderMessage = (
    message: ChatMessage,
    prevMessage: ChatMessage | null,
    embeddedInBatch = false,
  ) => (
    <MessageComponent
      key={getMessageKey(message)}
      message={message}
      prevMessage={prevMessage}
      createDiff={createDiff}
      onFileOpen={onFileOpen}
      onShowSettings={onShowSettings}
      onGrantToolPermission={onGrantToolPermission}
      autoExpandTools={autoExpandTools}
      showRawParameters={showRawParameters}
      showThinking={showThinking}
      selectedProject={selectedProject}
      provider={provider}
      embeddedInBatch={embeddedInBatch}
    />
  );

  const renderProcessBatch = (
    processMessages: ChatMessage[],
    previousMessage: ChatMessage,
    isComplete: boolean,
    batchKeyPrefix: string,
  ) => {
    const visibleProcessMessages = processMessages.filter((message) =>
      isVisibleProcessMessage(message, showThinking),
    );
    const firstProcessMessage = visibleProcessMessages[0];
    if (!firstProcessMessage) {
      return null;
    }

    const toolCallCount = visibleProcessMessages.filter((message) => message.isToolUse).length;
    const label = toolCallCount > 0
      ? t(isComplete ? 'tools.executedCalls' : 'tools.executingCalls', { count: toolCallCount })
      : t(isComplete ? 'process.completed' : 'process.running');

    return (
      <ToolCallBatch
        key={`${batchKeyPrefix}-${getMessageKey(firstProcessMessage)}-${visibleProcessMessages.length}`}
        count={toolCallCount}
        label={label}
        isComplete={isComplete}
      >
        <div className="space-y-2">
          {visibleProcessMessages.map((processMessage, processIndex) =>
            renderMessage(
              processMessage,
              processIndex > 0 ? visibleProcessMessages[processIndex - 1] : previousMessage,
              true,
            ),
          )}
        </div>
      </ToolCallBatch>
    );
  };

  const renderedMessages: React.ReactNode[] = [];
  groupedMessages.forEach((group, groupIndex) => {
    if (group.kind === 'standalone') {
      group.messages.forEach((message, messageIndex) => {
        const previousMessage = messageIndex > 0
          ? group.messages[messageIndex - 1]
          : group.startIndex > 0
            ? visibleMessages[group.startIndex - 1]
            : null;
        renderedMessages.push(renderMessage(message, previousMessage));
      });
      return;
    }

    const previousVisibleMessage =
      group.userIndex > 0 ? visibleMessages[group.userIndex - 1] : null;
    const belongsToActiveRound = isLoading && group.userIndex === latestUserMessageIndex;
    const isProcessBatchComplete = !belongsToActiveRound;

    renderedMessages.push(renderMessage(group.userMessage, previousVisibleMessage));

    const leadingBatch = renderProcessBatch(
      group.leadingProcessMessages,
      group.userMessage,
      isProcessBatchComplete,
      `turn-${groupIndex}-leading`,
    );
    if (leadingBatch) {
      renderedMessages.push(leadingBatch);
    }

    if (group.finalReply) {
      renderedMessages.push(renderMessage(group.finalReply, group.userMessage));
    }

    const trailingBatchAnchor = group.finalReply ?? group.userMessage;
    const trailingBatch = renderProcessBatch(
      group.trailingProcessMessages,
      trailingBatchAnchor,
      isProcessBatchComplete,
      `turn-${groupIndex}-trailing`,
    );
    if (trailingBatch) {
      renderedMessages.push(trailingBatch);
    }

    if (!group.finalReply && group.trailingVisibleMessage) {
      renderedMessages.push(renderMessage(group.trailingVisibleMessage, group.userMessage));
    }
  });

  return (
    <div
      ref={scrollContainerRef}
      onWheel={onWheel}
      onTouchMove={onTouchMove}
      className="relative flex-1 space-y-3 overflow-y-auto overflow-x-hidden px-0 py-3 sm:space-y-4 sm:p-4"
    >
      {isLoadingSessionMessages && chatMessages.length === 0 ? (
        <div className="mt-8 text-center text-gray-500 dark:text-gray-400">
          <div className="flex items-center justify-center space-x-2">
            <div className="h-4 w-4 animate-spin rounded-full border-b-2 border-gray-400" />
            <p>{t('session.loading.sessionMessages')}</p>
          </div>
        </div>
      ) : chatMessages.length === 0 ? (
        <ProviderSelectionEmptyState
          selectedSession={selectedSession}
          currentSessionId={currentSessionId}
          provider={provider}
          setProvider={setProvider}
          textareaRef={textareaRef}
          claudeModel={claudeModel}
          setClaudeModel={setClaudeModel}
          claudeThinkingEffort={claudeThinkingEffort}
          setClaudeThinkingEffort={setClaudeThinkingEffort}
          codexModel={codexModel}
          setCodexModel={setCodexModel}
          codexReasoningEffort={codexReasoningEffort}
          setCodexReasoningEffort={setCodexReasoningEffort}
          tasksEnabled={tasksEnabled}
          isTaskMasterInstalled={isTaskMasterInstalled}
          onShowAllTasks={onShowAllTasks}
          setInput={setInput}
        />
      ) : (
        <>
          {/* Loading indicator for older messages (hide when load-all is active) */}
          {isLoadingMoreMessages && !isLoadingAllMessages && !allMessagesLoaded && (
            <div className="py-3 text-center text-gray-500 dark:text-gray-400">
              <div className="flex items-center justify-center space-x-2">
                <div className="h-4 w-4 animate-spin rounded-full border-b-2 border-gray-400" />
                <p className="text-sm">{t('session.loading.olderMessages')}</p>
              </div>
            </div>
          )}

          {/* Indicator showing there are more messages to load (hide when all loaded) */}
          {hasMoreMessages && !isLoadingMoreMessages && !allMessagesLoaded && (
            <div className="border-b border-gray-200 py-2 text-center text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
              {totalMessages > 0 && (
                <span>
                  {t('session.messages.showingOf', { shown: sessionMessagesCount, total: totalMessages })}{' '}
                  <span className="text-xs">{t('session.messages.scrollToLoad')}</span>
                </span>
              )}
            </div>
          )}

          {/* Floating "Load all messages" overlay */}
          {(showLoadAllOverlay || isLoadingAllMessages || loadAllJustFinished) && (
            <div className="pointer-events-none sticky top-2 z-20 flex justify-center">
              {loadAllJustFinished ? (
                <div className="flex items-center space-x-2 rounded-full bg-green-600 px-4 py-1.5 text-xs font-medium text-white shadow-lg dark:bg-green-500">
                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>{t('session.messages.allLoaded')}</span>
                </div>
              ) : (
                <button
                  className="pointer-events-auto flex items-center space-x-2 rounded-full bg-blue-600 px-4 py-1.5 text-xs font-medium text-white shadow-lg transition-all duration-200 hover:scale-105 hover:bg-blue-700 disabled:cursor-wait disabled:opacity-75 dark:bg-blue-500 dark:hover:bg-blue-600"
                  onClick={loadAllMessages}
                  disabled={isLoadingAllMessages}
                >
                  {isLoadingAllMessages && (
                    <div className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  )}
                  <span>
                    {isLoadingAllMessages
                      ? t('session.messages.loadingAll')
                      : <>{t('session.messages.loadAll')} {totalMessages > 0 && `(${totalMessages})`}</>
                    }
                  </span>
                </button>
              )}
            </div>
          )}

          {/* Performance warning when all messages are loaded */}
          {allMessagesLoaded && (
            <div className="border-b border-amber-200 bg-amber-50 py-1.5 text-center text-xs text-amber-600 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-400">
              {t('session.messages.perfWarning')}
            </div>
          )}

          {/* Legacy message count indicator (for non-paginated view) */}
          {!hasMoreMessages && chatMessages.length > visibleMessageCount && (
            <div className="border-b border-gray-200 py-2 text-center text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
              {t('session.messages.showingLast', { count: visibleMessageCount, total: chatMessages.length })} |
              <button className="ml-1 text-blue-600 underline hover:text-blue-700" onClick={loadEarlierMessages}>
                {t('session.messages.loadEarlier')}
              </button>
              {' | '}
              <button
                className="text-blue-600 underline hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
                onClick={loadAllMessages}
              >
                {t('session.messages.loadAll')}
              </button>
            </div>
          )}

          {renderedMessages}
        </>
      )}

      {isLoading && <AssistantThinkingIndicator selectedProvider={provider} />}
    </div>
  );
}
