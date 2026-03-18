import { safeJsonParse } from '../../../lib/utils.js';
import type { ChatMessage, ClaudePermissionSuggestion, PermissionGrantResult } from '../types/types.js';
import { CLAUDE_SETTINGS_KEY, getClaudeSettings, safeLocalStorage } from './chatStorage';

const CLAUDE_PERMISSION_ERROR_PATTERNS = [
  /user denied tool use/i,
  /permission request timed out/i,
  /permission request cancelled/i,
  /tool disallowed by settings/i,
  /permission denied/i,
  /approval required/i,
  /requires (?:approval|permission)/i,
];

function stringifyToolResultContent(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isClaudePermissionError(message: ChatMessage | null | undefined): boolean {
  if (!message?.toolResult?.isError) return false;

  const parts = [
    stringifyToolResultContent(message.toolResult.content),
    stringifyToolResultContent(message.toolResult.toolUseResult),
    typeof message.content === 'string' ? message.content : '',
  ].filter(Boolean);

  if (parts.length === 0) {
    return false;
  }

  const errorText = parts.join('\n');
  return CLAUDE_PERMISSION_ERROR_PATTERNS.some((pattern) => pattern.test(errorText));
}

export function buildClaudeToolPermissionEntry(toolName?: string, toolInput?: unknown) {
  if (!toolName) return null;
  if (toolName !== 'Bash') return toolName;

  const parsed = safeJsonParse(toolInput);
  const command = typeof parsed?.command === 'string' ? parsed.command.trim() : '';
  if (!command) return toolName;

  const tokens = command.split(/\s+/);
  if (tokens.length === 0) return toolName;

  if (tokens[0] === 'git' && tokens[1]) {
    return `Bash(${tokens[0]} ${tokens[1]}:*)`;
  }
  return `Bash(${tokens[0]}:*)`;
}

export function formatToolInputForDisplay(input: unknown) {
  if (input === undefined || input === null) return '';
  if (typeof input === 'string') return input;
  try {
    return JSON.stringify(input, null, 2);
  } catch {
    return String(input);
  }
}

export function getClaudePermissionSuggestion(
  message: ChatMessage | null | undefined,
  provider: string,
): ClaudePermissionSuggestion | null {
  if (provider !== 'claude') return null;
  if (!message?.toolResult?.isError) return null;
  if (!isClaudePermissionError(message)) return null;

  const toolName = message?.toolName;
  const entry = buildClaudeToolPermissionEntry(toolName, message.toolInput);
  if (!entry) return null;

  const settings = getClaudeSettings();
  const isAllowed = settings.allowedTools.includes(entry);
  return { toolName: toolName || 'UnknownTool', entry, isAllowed };
}

export function grantClaudeToolPermission(entry: string | null): PermissionGrantResult {
  if (!entry) return { success: false };

  const settings = getClaudeSettings();
  const alreadyAllowed = settings.allowedTools.includes(entry);
  const nextAllowed = alreadyAllowed ? settings.allowedTools : [...settings.allowedTools, entry];
  const nextDisallowed = settings.disallowedTools.filter((tool) => tool !== entry);
  const updatedSettings = {
    ...settings,
    allowedTools: nextAllowed,
    disallowedTools: nextDisallowed,
    lastUpdated: new Date().toISOString(),
  };

  safeLocalStorage.setItem(CLAUDE_SETTINGS_KEY, JSON.stringify(updatedSettings));
  return { success: true, alreadyAllowed, updatedSettings };
}
