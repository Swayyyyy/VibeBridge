import { IS_PLATFORM } from '../constants/config';
import { CLAUDE_MODELS, CODEX_MODELS } from '../../shared/modelConstants';

export type ProviderThinkingEffort = 'low' | 'medium' | 'high' | 'ultra-high';
export type StoredProjectSortOrder = 'name' | 'date';
export type StoredCodexPermissionMode = 'default' | 'acceptEdits' | 'bypassPermissions';

export type ClaudeSettingsStorage = {
  allowedTools: string[];
  disallowedTools: string[];
  skipPermissions: boolean;
  projectSortOrder: StoredProjectSortOrder;
  command: string;
  model: string;
  thinkingEffort: ProviderThinkingEffort;
  lastUpdated?: string;
};

export type CodexSettingsStorage = {
  permissionMode: StoredCodexPermissionMode;
  command: string;
  model: string;
  reasoningEffort: ProviderThinkingEffort;
  lastUpdated?: string;
};

export const CLAUDE_SETTINGS_STORAGE_KEY = 'claude-settings';
export const CODEX_SETTINGS_STORAGE_KEY = 'codex-settings';
export const CLAUDE_MODEL_STORAGE_KEY = 'claude-model';
export const CODEX_MODEL_STORAGE_KEY = 'codex-model';
export const PROVIDER_SETTINGS_CHANGED_EVENT = 'provider-settings-changed';

const PROVIDER_THINKING_EFFORT_VALUES: ProviderThinkingEffort[] = [
  'low',
  'medium',
  'high',
  'ultra-high',
];

const DEFAULT_CLAUDE_SETTINGS: ClaudeSettingsStorage = {
  allowedTools: [],
  disallowedTools: [],
  skipPermissions: false,
  projectSortOrder: 'name',
  command: '',
  model: CLAUDE_MODELS.DEFAULT,
  thinkingEffort: 'medium',
};

const DEFAULT_CODEX_SETTINGS: CodexSettingsStorage = {
  permissionMode: 'default',
  command: '',
  model: CODEX_MODELS.DEFAULT,
  reasoningEffort: 'medium',
};

const parseJson = <T>(raw: string | null, fallback: T): T => {
  if (!raw) {
    return fallback;
  }

  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
};

const safeGetLocalStorage = (key: string): string | null => {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const safeSetLocalStorage = (key: string, value: string) => {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore storage failures and keep the in-memory state usable.
  }
};

const dispatchProviderSettingsChanged = () => {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new Event(PROVIDER_SETTINGS_CHANGED_EVENT));
};

const isKnownClaudeModel = (value: unknown): value is string => (
  typeof value === 'string' && CLAUDE_MODELS.OPTIONS.some((option) => option.value === value)
);

const isKnownCodexModel = (value: unknown): value is string => (
  typeof value === 'string' && CODEX_MODELS.OPTIONS.some((option) => option.value === value)
);

export const normalizeThinkingEffort = (
  value: unknown,
  fallback: ProviderThinkingEffort = 'medium',
): ProviderThinkingEffort => {
  if (
    typeof value === 'string' &&
    PROVIDER_THINKING_EFFORT_VALUES.includes(value as ProviderThinkingEffort)
  ) {
    return value as ProviderThinkingEffort;
  }

  return fallback;
};

export const normalizeClaudeSettings = (
  raw: Partial<ClaudeSettingsStorage> | null | undefined,
): ClaudeSettingsStorage => {
  const legacyModel = safeGetLocalStorage(CLAUDE_MODEL_STORAGE_KEY);

  return {
    allowedTools: Array.isArray(raw?.allowedTools) ? raw.allowedTools : [],
    disallowedTools: Array.isArray(raw?.disallowedTools) ? raw.disallowedTools : [],
    skipPermissions: Boolean(raw?.skipPermissions),
    projectSortOrder: raw?.projectSortOrder === 'date' ? 'date' : 'name',
    command: typeof raw?.command === 'string' ? raw.command : '',
    model: isKnownClaudeModel(raw?.model)
      ? raw.model
      : isKnownClaudeModel(legacyModel)
        ? legacyModel
        : DEFAULT_CLAUDE_SETTINGS.model,
    thinkingEffort: normalizeThinkingEffort(raw?.thinkingEffort, DEFAULT_CLAUDE_SETTINGS.thinkingEffort),
    lastUpdated: typeof raw?.lastUpdated === 'string' ? raw.lastUpdated : undefined,
  };
};

export const normalizeCodexSettings = (
  raw: Partial<CodexSettingsStorage> | null | undefined,
): CodexSettingsStorage => {
  const legacyModel = safeGetLocalStorage(CODEX_MODEL_STORAGE_KEY);

  return {
    permissionMode:
      raw?.permissionMode === 'acceptEdits' || raw?.permissionMode === 'bypassPermissions'
        ? raw.permissionMode
        : 'default',
    command: typeof raw?.command === 'string' ? raw.command : '',
    model: isKnownCodexModel(raw?.model)
      ? raw.model
      : isKnownCodexModel(legacyModel)
        ? legacyModel
        : DEFAULT_CODEX_SETTINGS.model,
    reasoningEffort: normalizeThinkingEffort(raw?.reasoningEffort, DEFAULT_CODEX_SETTINGS.reasoningEffort),
    lastUpdated: typeof raw?.lastUpdated === 'string' ? raw.lastUpdated : undefined,
  };
};

export const getStoredClaudeSettings = (): ClaudeSettingsStorage => {
  const raw = parseJson<Partial<ClaudeSettingsStorage>>(
    safeGetLocalStorage(CLAUDE_SETTINGS_STORAGE_KEY),
    {},
  );
  return normalizeClaudeSettings(raw);
};

export const getStoredCodexSettings = (): CodexSettingsStorage => {
  const raw = parseJson<Partial<CodexSettingsStorage>>(
    safeGetLocalStorage(CODEX_SETTINGS_STORAGE_KEY),
    {},
  );
  return normalizeCodexSettings(raw);
};

export const saveStoredClaudeSettings = (settings: Partial<ClaudeSettingsStorage>): ClaudeSettingsStorage => {
  const normalized = normalizeClaudeSettings(settings);
  safeSetLocalStorage(CLAUDE_SETTINGS_STORAGE_KEY, JSON.stringify(normalized));
  safeSetLocalStorage(CLAUDE_MODEL_STORAGE_KEY, normalized.model);
  dispatchProviderSettingsChanged();
  return normalized;
};

export const saveStoredCodexSettings = (settings: Partial<CodexSettingsStorage>): CodexSettingsStorage => {
  const normalized = normalizeCodexSettings(settings);
  safeSetLocalStorage(CODEX_SETTINGS_STORAGE_KEY, JSON.stringify(normalized));
  safeSetLocalStorage(CODEX_MODEL_STORAGE_KEY, normalized.model);
  dispatchProviderSettingsChanged();
  return normalized;
};

export const getDefaultProviderLoginCommand = ({
  provider,
  isAuthenticated,
  isOnboarding,
}: {
  provider: 'claude' | 'codex';
  isAuthenticated: boolean;
  isOnboarding: boolean;
}) => {
  if (provider === 'claude') {
    if (isAuthenticated) {
      return 'claude setup-token --dangerously-skip-permissions';
    }

    return isOnboarding
      ? 'claude /exit --dangerously-skip-permissions'
      : 'claude /login --dangerously-skip-permissions';
  }

  return IS_PLATFORM ? 'codex login --device-auth' : 'codex login';
};
