import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '../../../shared/view/ui';
import SettingsSidebar from '../view/SettingsSidebar';
import AccountSettingsTab from '../view/tabs/AccountSettingsTab';
import AgentsSettingsTab from '../view/tabs/agents-settings/AgentsSettingsTab';
import AppearanceSettingsTab from '../view/tabs/AppearanceSettingsTab';
import GitSettingsTab from '../view/tabs/git-settings/GitSettingsTab';
import { useSettingsController } from '../hooks/useSettingsController';
import type { SettingsProps } from '../types/types';

function Settings({ isOpen, onClose, projects = [], initialTab = 'agents' }: SettingsProps) {
  const { t } = useTranslation('settings');
  const {
    activeTab,
    setActiveTab,
    saveStatus,
    projectSortOrder,
    setProjectSortOrder,
    codeEditorSettings,
    updateCodeEditorSetting,
    claudePermissions,
    setClaudePermissions,
    claudeAccountSettings,
    setClaudeAccountSettings,
    codexAccountSettings,
    setCodexAccountSettings,
    codexPermissionMode,
    setCodexPermissionMode,
  } = useSettingsController({
    isOpen,
    initialTab,
    projects,
    onClose,
  });

  if (!isOpen) {
    return null;
  }

  return (
    <div className="modal-backdrop fixed inset-0 z-[9999] flex items-center justify-center bg-background/80 backdrop-blur-sm md:p-4">
      <div className="flex h-full w-full flex-col overflow-hidden border border-border bg-background shadow-2xl md:h-[90vh] md:max-w-4xl md:rounded-xl">
        {/* Header */}
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-3 md:px-5">
          <h2 className="text-base font-semibold text-foreground">{t('title')}</h2>
          <div className="flex items-center gap-2">
            {saveStatus === 'success' && (
              <span className="text-xs text-muted-foreground animate-in fade-in">{t('saveStatus.success')}</span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="h-10 w-10 touch-manipulation p-0 text-muted-foreground hover:text-foreground active:bg-accent/50"
            >
              <X className="h-5 w-5" />
            </Button>
          </div>
        </div>

        {/* Body: sidebar + content */}
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <SettingsSidebar activeTab={activeTab} onChange={setActiveTab} />

          {/* Content */}
          <main className="flex-1 overflow-y-auto">
            <div key={activeTab} className="settings-content-enter space-y-6 p-4 pb-safe-area-inset-bottom md:space-y-8 md:p-6">
              {activeTab === 'appearance' && (
                <AppearanceSettingsTab
                  projectSortOrder={projectSortOrder}
                  onProjectSortOrderChange={setProjectSortOrder}
                  codeEditorSettings={codeEditorSettings}
                  onCodeEditorThemeChange={(value) => updateCodeEditorSetting('theme', value)}
                  onCodeEditorWordWrapChange={(value) => updateCodeEditorSetting('wordWrap', value)}
                  onCodeEditorShowMinimapChange={(value) => updateCodeEditorSetting('showMinimap', value)}
                  onCodeEditorLineNumbersChange={(value) => updateCodeEditorSetting('lineNumbers', value)}
                  onCodeEditorFontSizeChange={(value) => updateCodeEditorSetting('fontSize', value)}
                />
              )}

              {activeTab === 'account' && <AccountSettingsTab />}

              {activeTab === 'git' && <GitSettingsTab />}

              {activeTab === 'agents' && (
                <AgentsSettingsTab
                  claudeAccountSettings={claudeAccountSettings}
                  onClaudeAccountSettingsChange={setClaudeAccountSettings}
                  claudePermissions={claudePermissions}
                  onClaudePermissionsChange={setClaudePermissions}
                  codexAccountSettings={codexAccountSettings}
                  onCodexAccountSettingsChange={setCodexAccountSettings}
                  codexPermissionMode={codexPermissionMode}
                  onCodexPermissionModeChange={setCodexPermissionMode}
                />
              )}
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}

export default Settings;
