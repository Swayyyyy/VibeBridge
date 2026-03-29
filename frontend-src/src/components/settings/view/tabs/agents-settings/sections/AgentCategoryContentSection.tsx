import type { AgentCategoryContentSectionProps } from '../types';
import AccountContent from './content/AccountContent';
import PermissionsContent from './content/PermissionsContent';

export default function AgentCategoryContentSection({
  selectedAgent,
  selectedCategory,
  claudeAccountSettings,
  onClaudeAccountSettingsChange,
  claudePermissions,
  onClaudePermissionsChange,
  codexAccountSettings,
  onCodexAccountSettingsChange,
  codexPermissionMode,
  onCodexPermissionModeChange,
}: AgentCategoryContentSectionProps) {
  return (
    <div className="flex-1 overflow-y-auto p-3 md:p-4">
      {selectedCategory === 'account' && (
        <AccountContent
          agent={selectedAgent}
          accountSettings={selectedAgent === 'claude' ? claudeAccountSettings : codexAccountSettings}
          onAccountSettingsChange={(value) => {
            if (selectedAgent === 'claude') {
              onClaudeAccountSettingsChange(value as typeof claudeAccountSettings);
              return;
            }

            onCodexAccountSettingsChange(value as typeof codexAccountSettings);
          }}
        />
      )}

      {selectedCategory === 'permissions' && selectedAgent === 'claude' && (
        <PermissionsContent
          agent="claude"
          skipPermissions={claudePermissions.skipPermissions}
          onSkipPermissionsChange={(value) => {
            onClaudePermissionsChange({ ...claudePermissions, skipPermissions: value });
          }}
          allowedTools={claudePermissions.allowedTools}
          onAllowedToolsChange={(value) => {
            onClaudePermissionsChange({ ...claudePermissions, allowedTools: value });
          }}
          disallowedTools={claudePermissions.disallowedTools}
          onDisallowedToolsChange={(value) => {
            onClaudePermissionsChange({ ...claudePermissions, disallowedTools: value });
          }}
        />
      )}

      {selectedCategory === 'permissions' && selectedAgent === 'codex' && (
        <PermissionsContent
          agent="codex"
          permissionMode={codexPermissionMode}
          onPermissionModeChange={onCodexPermissionModeChange}
        />
      )}
    </div>
  );
}
