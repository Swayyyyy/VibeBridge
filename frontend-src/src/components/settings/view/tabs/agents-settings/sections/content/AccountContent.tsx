import { ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CLAUDE_MODELS, CODEX_MODELS } from '../../../../../../../../shared/modelConstants';
import { type ProviderThinkingEffort } from '../../../../../../../shared/providerSettings';
import { Input } from '../../../../../../../shared/view/ui';
import SessionProviderLogo from '../../../../../../llm-logo-provider/SessionProviderLogo';
import type {
  AgentProvider,
  ClaudeAccountSettingsState,
  CodexAccountSettingsState,
} from '../../../../../types/types';

type AccountContentProps = {
  agent: AgentProvider;
  accountSettings: ClaudeAccountSettingsState | CodexAccountSettingsState;
  onAccountSettingsChange: (value: ClaudeAccountSettingsState | CodexAccountSettingsState) => void;
};

type AgentVisualConfig = {
  name: string;
  bgClass: string;
  borderClass: string;
  textClass: string;
  subtextClass: string;
  buttonClass: string;
  description?: string;
};

const agentConfig: Record<AgentProvider, AgentVisualConfig> = {
  claude: {
    name: 'Claude',
    bgClass: 'bg-blue-50 dark:bg-blue-900/20',
    borderClass: 'border-blue-200 dark:border-blue-800',
    textClass: 'text-blue-900 dark:text-blue-100',
    subtextClass: 'text-blue-700 dark:text-blue-300',
    buttonClass: 'bg-blue-600 hover:bg-blue-700 active:bg-blue-800',
  },
  codex: {
    name: 'Codex',
    bgClass: 'bg-muted/50',
    borderClass: 'border-gray-300 dark:border-gray-600',
    textClass: 'text-gray-900 dark:text-gray-100',
    subtextClass: 'text-gray-700 dark:text-gray-300',
    buttonClass: 'bg-gray-800 hover:bg-gray-900 active:bg-gray-950 dark:bg-gray-700 dark:hover:bg-gray-600 dark:active:bg-gray-500',
  },
};

const THINKING_EFFORT_VALUES: ProviderThinkingEffort[] = ['low', 'medium', 'high', 'ultra-high'];

export default function AccountContent({
  agent,
  accountSettings,
  onAccountSettingsChange,
}: AccountContentProps) {
  const { t } = useTranslation('settings');
  const config = agentConfig[agent];
  const modelOptions = agent === 'claude' ? CLAUDE_MODELS.OPTIONS : CODEX_MODELS.OPTIONS;
  const commandPlaceholder = agent === 'claude'
    ? t('agents.account.placeholders.claudeCommand', { defaultValue: 'claude' })
    : t('agents.account.placeholders.codexCommand', { defaultValue: 'codex' });
  const effortLabel = t('agents.account.fields.strength', { defaultValue: 'Strength' });
  const effortLabels: Record<ProviderThinkingEffort, string> = {
    low: t('agents.account.efforts.low', { defaultValue: 'Low' }),
    medium: t('agents.account.efforts.medium', { defaultValue: 'Medium' }),
    high: t('agents.account.efforts.high', { defaultValue: 'High' }),
    'ultra-high': t('agents.account.efforts.ultraHigh', { defaultValue: 'Ultra-high' }),
  };

  return (
    <div className="space-y-6">
      <div className="mb-4 flex items-center gap-3">
        <SessionProviderLogo provider={agent} className="h-6 w-6" />
        <div>
          <h3 className="text-lg font-medium text-foreground">{config.name}</h3>
          <p className="text-sm text-muted-foreground">{t(`agents.account.${agent}.description`)}</p>
        </div>
      </div>

      <div className={`${config.bgClass} border ${config.borderClass} rounded-lg p-4`}>
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <label className={`text-sm font-medium ${config.textClass}`}>
                {t('agents.account.fields.command', { defaultValue: 'Command' })}
              </label>
              <Input
                value={accountSettings.command}
                onChange={(event) => {
                  onAccountSettingsChange({
                    ...accountSettings,
                    command: event.target.value,
                  });
                }}
                placeholder={commandPlaceholder}
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
              />
            </div>

            <div className="space-y-2">
              <label className={`text-sm font-medium ${config.textClass}`}>
                {t('agents.account.fields.model', { defaultValue: 'Model' })}
              </label>
              <div className="relative">
                <select
                  value={accountSettings.model}
                  onChange={(event) => {
                    onAccountSettingsChange({
                      ...accountSettings,
                      model: event.target.value,
                    });
                  }}
                  className="h-9 w-full appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  {modelOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              </div>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <label className={`text-sm font-medium ${config.textClass}`}>{effortLabel}</label>
              <div className="relative">
                <select
                  value={
                    'thinkingEffort' in accountSettings
                      ? accountSettings.thinkingEffort
                      : accountSettings.reasoningEffort
                  }
                  onChange={(event) => {
                    const nextValue = event.target.value as ProviderThinkingEffort;
                    onAccountSettingsChange(
                      'thinkingEffort' in accountSettings
                        ? {
                            ...accountSettings,
                            thinkingEffort: nextValue,
                          }
                        : {
                            ...accountSettings,
                            reasoningEffort: nextValue,
                          },
                    );
                  }}
                  className="h-9 w-full appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  {THINKING_EFFORT_VALUES.map((value) => (
                    <option key={value} value={value}>
                      {effortLabels[value]}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
