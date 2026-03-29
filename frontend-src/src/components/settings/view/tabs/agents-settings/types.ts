import type {
  AgentProvider,
  AgentCategory,
  ClaudeAccountSettingsState,
  ClaudePermissionsState,
  CodexAccountSettingsState,
  CodexPermissionMode,
} from '../../../types/types';

export type AgentsSettingsTabProps = {
  claudeAccountSettings: ClaudeAccountSettingsState;
  onClaudeAccountSettingsChange: (value: ClaudeAccountSettingsState) => void;
  claudePermissions: ClaudePermissionsState;
  onClaudePermissionsChange: (value: ClaudePermissionsState) => void;
  codexAccountSettings: CodexAccountSettingsState;
  onCodexAccountSettingsChange: (value: CodexAccountSettingsState) => void;
  codexPermissionMode: CodexPermissionMode;
  onCodexPermissionModeChange: (value: CodexPermissionMode) => void;
};

export type AgentCategoryTabsSectionProps = {
  selectedCategory: AgentCategory;
  onSelectCategory: (category: AgentCategory) => void;
};

export type AgentSelectorSectionProps = {
  selectedAgent: AgentProvider;
  onSelectAgent: (agent: AgentProvider) => void;
};

export type AgentCategoryContentSectionProps = {
  selectedAgent: AgentProvider;
  selectedCategory: AgentCategory;
  claudeAccountSettings: ClaudeAccountSettingsState;
  onClaudeAccountSettingsChange: (value: ClaudeAccountSettingsState) => void;
  claudePermissions: ClaudePermissionsState;
  onClaudePermissionsChange: (value: ClaudePermissionsState) => void;
  codexAccountSettings: CodexAccountSettingsState;
  onCodexAccountSettingsChange: (value: CodexAccountSettingsState) => void;
  codexPermissionMode: CodexPermissionMode;
  onCodexPermissionModeChange: (value: CodexPermissionMode) => void;
};
