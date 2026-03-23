import { memo, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface ToolCallBatchProps {
  count?: number;
  label?: string;
  isComplete: boolean;
  children: React.ReactNode;
}

const ToolCallBatch = memo(({ count, label, isComplete, children }: ToolCallBatchProps) => {
  const { t } = useTranslation('chat');
  const [isOpen, setIsOpen] = useState(!isComplete);
  const previousCompleteRef = useRef(isComplete);

  useEffect(() => {
    if (previousCompleteRef.current === isComplete) {
      return;
    }

    setIsOpen(!isComplete);
    previousCompleteRef.current = isComplete;
  }, [isComplete]);

  const title =
    label ??
    t(isComplete ? 'tools.executedCalls' : 'tools.executingCalls', { count: count ?? 0 });

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-gray-200/70 bg-gray-50/80 dark:border-gray-800/70 dark:bg-gray-900/30">
      <button
        type="button"
        onClick={() => setIsOpen((current) => !current)}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-gray-100/80 dark:hover:bg-gray-800/40"
      >
        <svg
          className={`h-3.5 w-3.5 flex-shrink-0 text-gray-400 transition-transform duration-150 dark:text-gray-500 ${isOpen ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
          {title}
        </span>
      </button>

      {isOpen && (
        <div className="border-t border-gray-200/70 px-3 py-2 dark:border-gray-800/70">
          {children}
        </div>
      )}
    </div>
  );
});

ToolCallBatch.displayName = 'ToolCallBatch';

export default ToolCallBatch;
