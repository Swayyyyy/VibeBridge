import React from 'react';
import { ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CLAUDE_MODELS, CODEX_MODELS } from '../../../../../shared/modelConstants';
import type { ProviderThinkingEffort } from '../../../../shared/providerSettings';
import type { PermissionMode, Provider } from '../../types/types';
import TokenUsagePie from './TokenUsagePie';

interface ChatInputControlsProps {
  permissionMode: PermissionMode | string;
  onModeSwitch: () => void;
  provider: Provider | string;
  claudeModel: string;
  setClaudeModel: (model: string) => void;
  claudeThinkingEffort: ProviderThinkingEffort;
  setClaudeThinkingEffort: (effort: ProviderThinkingEffort) => void;
  codexModel: string;
  setCodexModel: (model: string) => void;
  codexReasoningEffort: ProviderThinkingEffort;
  setCodexReasoningEffort: (effort: ProviderThinkingEffort) => void;
  tokenBudget: { used?: number; total?: number } | null;
  hasInput: boolean;
  onClearInput: () => void;
  isUserScrolledUp: boolean;
  hasMessages: boolean;
  onScrollToBottom: () => void;
}

const THINKING_EFFORT_VALUES: ProviderThinkingEffort[] = ['low', 'medium', 'high', 'ultra-high'];

export default function ChatInputControls({
  permissionMode,
  onModeSwitch,
  provider,
  claudeModel,
  setClaudeModel,
  claudeThinkingEffort,
  setClaudeThinkingEffort,
  codexModel,
  setCodexModel,
  codexReasoningEffort,
  setCodexReasoningEffort,
  tokenBudget,
  hasInput,
  onClearInput,
  isUserScrolledUp,
  hasMessages,
  onScrollToBottom,
}: ChatInputControlsProps) {
  const { t } = useTranslation('chat');
  const modelOptions = provider === 'codex' ? CODEX_MODELS.OPTIONS : CLAUDE_MODELS.OPTIONS;
  const currentModel = provider === 'codex' ? codexModel : claudeModel;
  const currentEffort = provider === 'codex' ? codexReasoningEffort : claudeThinkingEffort;
  const effortLabels: Record<ProviderThinkingEffort, string> = {
    low: t('thinkingMode.simple.low', { defaultValue: 'Low' }),
    medium: t('thinkingMode.simple.medium', { defaultValue: 'Medium' }),
    high: t('thinkingMode.simple.high', { defaultValue: 'High' }),
    'ultra-high': t('thinkingMode.simple.ultraHigh', { defaultValue: 'Ultra-high' }),
  };

  const handleModelChange = (value: string) => {
    if (provider === 'codex') {
      setCodexModel(value);
      return;
    }

    setClaudeModel(value);
  };

  const handleEffortChange = (value: ProviderThinkingEffort) => {
    if (provider === 'codex') {
      setCodexReasoningEffort(value);
      return;
    }

    setClaudeThinkingEffort(value);
  };

  return (
    <div className="flex flex-wrap items-center justify-center gap-2 sm:gap-3">
      <button
        type="button"
        onClick={onModeSwitch}
        className={`rounded-lg border px-2.5 py-1 text-sm font-medium transition-all duration-200 sm:px-3 sm:py-1.5 ${
          permissionMode === 'default'
            ? 'border-border/60 bg-muted/50 text-muted-foreground hover:bg-muted'
            : permissionMode === 'acceptEdits'
              ? 'border-green-300/60 bg-green-50 text-green-700 hover:bg-green-100 dark:border-green-600/40 dark:bg-green-900/15 dark:text-green-300 dark:hover:bg-green-900/25'
              : permissionMode === 'bypassPermissions'
                ? 'border-orange-300/60 bg-orange-50 text-orange-700 hover:bg-orange-100 dark:border-orange-600/40 dark:bg-orange-900/15 dark:text-orange-300 dark:hover:bg-orange-900/25'
                : 'border-primary/20 bg-primary/5 text-primary hover:bg-primary/10'
        }`}
        title={t('input.clickToChangeMode')}
      >
        <div className="flex items-center gap-1.5">
          <div
            className={`h-1.5 w-1.5 rounded-full ${
              permissionMode === 'default'
                ? 'bg-muted-foreground'
                : permissionMode === 'acceptEdits'
                  ? 'bg-green-500'
                  : permissionMode === 'bypassPermissions'
                    ? 'bg-orange-500'
                    : 'bg-primary'
            }`}
          />
          <span>
            {permissionMode === 'default' && t('codex.modes.default')}
            {permissionMode === 'acceptEdits' && t('codex.modes.acceptEdits')}
            {permissionMode === 'bypassPermissions' && t('codex.modes.bypassPermissions')}
            {permissionMode === 'plan' && t('codex.modes.plan')}
          </span>
        </div>
      </button>

      <div className="relative">
        <select
          value={currentModel}
          onChange={(event) => handleModelChange(event.target.value)}
          className="h-8 appearance-none rounded-lg border border-border/60 bg-muted/50 py-1 pl-3 pr-8 text-sm font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-primary/20"
          aria-label={t('providerSelection.selectModel', { defaultValue: 'Select model' })}
          title={t('providerSelection.selectModel', { defaultValue: 'Select model' })}
        >
          {modelOptions.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      </div>

      <div className="relative">
        <select
          value={currentEffort}
          onChange={(event) => handleEffortChange(event.target.value as ProviderThinkingEffort)}
          className="h-8 appearance-none rounded-lg border border-border/60 bg-muted/50 py-1 pl-3 pr-8 text-sm font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-primary/20"
          aria-label={t('providerSelection.selectStrength', { defaultValue: 'Strength' })}
          title={t('providerSelection.selectStrength', { defaultValue: 'Strength' })}
        >
          {THINKING_EFFORT_VALUES.map((value) => (
            <option key={value} value={value}>
              {effortLabels[value]}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      </div>

      <TokenUsagePie used={tokenBudget?.used || 0} total={tokenBudget?.total || parseInt(import.meta.env.VITE_CONTEXT_WINDOW) || 160000} />

      {hasInput && (
        <button
          type="button"
          onClick={onClearInput}
          className="group flex h-7 w-7 items-center justify-center rounded-lg border border-border/50 bg-card shadow-sm transition-all duration-200 hover:bg-accent/60 sm:h-8 sm:w-8"
          title={t('input.clearInput', { defaultValue: 'Clear input' })}
        >
          <svg
            className="h-3.5 w-3.5 text-muted-foreground transition-colors group-hover:text-foreground sm:h-4 sm:w-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}

      {isUserScrolledUp && hasMessages && (
        <button
          onClick={onScrollToBottom}
          className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm transition-all duration-200 hover:scale-105 hover:bg-primary/90 sm:h-8 sm:w-8"
          title={t('input.scrollToBottom', { defaultValue: 'Scroll to bottom' })}
        >
          <svg className="h-3.5 w-3.5 sm:h-4 sm:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
          </svg>
        </button>
      )}
    </div>
  );
}
