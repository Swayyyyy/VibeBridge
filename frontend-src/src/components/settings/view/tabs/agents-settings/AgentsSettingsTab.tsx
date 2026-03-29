import { useState } from 'react';
import type { AgentCategory, AgentProvider } from '../../../types/types';
import AgentCategoryContentSection from './sections/AgentCategoryContentSection';
import AgentCategoryTabsSection from './sections/AgentCategoryTabsSection';
import AgentSelectorSection from './sections/AgentSelectorSection';
import type { AgentsSettingsTabProps } from './types';

export default function AgentsSettingsTab({
  claudeAccountSettings,
  onClaudeAccountSettingsChange,
  claudePermissions,
  onClaudePermissionsChange,
  codexAccountSettings,
  onCodexAccountSettingsChange,
  codexPermissionMode,
  onCodexPermissionModeChange,
}: AgentsSettingsTabProps) {
  const [selectedAgent, setSelectedAgent] = useState<AgentProvider>('claude');
  const [selectedCategory, setSelectedCategory] = useState<AgentCategory>('account');

  return (
    <div className="-mx-4 -mb-4 -mt-2 flex min-h-[300px] flex-col overflow-hidden md:-mx-6 md:-mb-6 md:-mt-2 md:min-h-[500px]">
      <AgentSelectorSection
        selectedAgent={selectedAgent}
        onSelectAgent={setSelectedAgent}
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <AgentCategoryTabsSection
          selectedCategory={selectedCategory}
          onSelectCategory={setSelectedCategory}
        />

        <AgentCategoryContentSection
          selectedAgent={selectedAgent}
          selectedCategory={selectedCategory}
          claudeAccountSettings={claudeAccountSettings}
          onClaudeAccountSettingsChange={onClaudeAccountSettingsChange}
          claudePermissions={claudePermissions}
          onClaudePermissionsChange={onClaudePermissionsChange}
          codexAccountSettings={codexAccountSettings}
          onCodexAccountSettingsChange={onCodexAccountSettingsChange}
          codexPermissionMode={codexPermissionMode}
          onCodexPermissionModeChange={onCodexPermissionModeChange}
        />
      </div>
    </div>
  );
}
