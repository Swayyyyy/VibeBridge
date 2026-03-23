import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { QueuedPromptItem } from '../../types/types';

interface QueuedPromptPanelProps {
  queuedPrompts: QueuedPromptItem[];
  onGuideQueuedPrompt: (queuePromptId: string) => void;
  onCancelQueuedPrompt: (queuePromptId: string) => void;
  onEditQueuedPrompt: (queuePromptId: string) => void;
}

export default function QueuedPromptPanel({
  queuedPrompts,
  onGuideQueuedPrompt,
  onCancelQueuedPrompt,
  onEditQueuedPrompt,
}: QueuedPromptPanelProps) {
  const { t } = useTranslation('chat');
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [menuPromptId, setMenuPromptId] = useState<string | null>(null);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!menuPromptId) {
        return;
      }

      const target = event.target as Node;
      if (panelRef.current && !panelRef.current.contains(target)) {
        setMenuPromptId(null);
      }
    };

    window.addEventListener('mousedown', handlePointerDown);
    return () => {
      window.removeEventListener('mousedown', handlePointerDown);
    };
  }, [menuPromptId]);

  useEffect(() => {
    if (queuedPrompts.length === 0) {
      setMenuPromptId(null);
      return;
    }

    if (menuPromptId && !queuedPrompts.some((queueItem) => queueItem.id === menuPromptId)) {
      setMenuPromptId(null);
    }
  }, [menuPromptId, queuedPrompts]);

  if (queuedPrompts.length === 0) {
    return null;
  }

  return (
    <div ref={panelRef} className="mx-auto mb-3 max-w-4xl space-y-2">
      {queuedPrompts.map((queueItem) => {
        const isMenuOpen = menuPromptId === queueItem.id;

        return (
          <div
            key={queueItem.id}
            className={`relative overflow-visible rounded-[1.75rem] border border-border/70 bg-card/95 shadow-sm backdrop-blur-sm ${
              isMenuOpen ? 'z-30' : 'z-0'
            }`}
          >
            <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center">
              <div className="hidden h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-muted/80 text-muted-foreground sm:flex">
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.8}
                    d="M9 7H5v4m0-4l5 5a7 7 0 1012 4"
                  />
                </svg>
              </div>

              <div className="min-w-0 flex-1">
                <div className="whitespace-pre-wrap break-words text-base font-medium leading-7 text-foreground">
                  {queueItem.prompt}
                </div>
              </div>

              <div className="flex flex-shrink-0 items-center justify-end gap-1">
                <button
                  type="button"
                  onClick={() => onGuideQueuedPrompt(queueItem.id)}
                  className="inline-flex h-11 items-center gap-2 rounded-full bg-muted px-3.5 text-sm font-medium text-foreground transition-colors hover:bg-muted/80"
                  title={t('queuedPrompt.guide', { defaultValue: 'Run next' })}
                  aria-label={t('queuedPrompt.guide', { defaultValue: 'Run next' })}
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.9}
                      d="M4 12h8m0 0L8.5 8.5M12 12l-3.5 3.5M14 6h3a3 3 0 013 3v6a3 3 0 01-3 3h-3"
                    />
                  </svg>
                  <span>{t('queuedPrompt.guide', { defaultValue: 'Run next' })}</span>
                </button>

                <button
                  type="button"
                  onClick={() => onCancelQueuedPrompt(queueItem.id)}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  title={t('queuedPrompt.cancel', { defaultValue: 'Remove from queue' })}
                  aria-label={t('queuedPrompt.cancel', { defaultValue: 'Remove from queue' })}
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.9}
                      d="M6 7h12m-9 0V5a1 1 0 011-1h4a1 1 0 011 1v2m-8 0l.6 11a2 2 0 002 1.9h4.8a2 2 0 002-1.9L17 7M10 11v6m4-6v6"
                    />
                  </svg>
                </button>

                <div className="relative">
                  <button
                    type="button"
                    onClick={() =>
                      setMenuPromptId((previous) => (previous === queueItem.id ? null : queueItem.id))
                    }
                    className="inline-flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    title={t('queuedPrompt.more', { defaultValue: 'More' })}
                    aria-label={t('queuedPrompt.more', { defaultValue: 'More' })}
                  >
                    <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
                      <path d="M10 6a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 5.5A1.5 1.5 0 1010 8a1.5 1.5 0 000 3.5zM11.5 15.5a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z" />
                    </svg>
                  </button>

                  {isMenuOpen && (
                    <div className="absolute bottom-full right-0 z-40 mb-2 min-w-40 rounded-2xl border border-border bg-popover p-1.5 shadow-xl">
                      <button
                        type="button"
                        onClick={() => {
                          setMenuPromptId(null);
                          onEditQueuedPrompt(queueItem.id);
                        }}
                        className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-medium text-popover-foreground transition-colors hover:bg-muted"
                      >
                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={1.9}
                            d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.5-8.5a2.1 2.1 0 113 3L12 16l-4 1 1-4 8.5-8.5z"
                          />
                        </svg>
                        <span>{t('queuedPrompt.edit', { defaultValue: 'Edit message' })}</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
