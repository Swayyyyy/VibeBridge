import { useMemo, useState } from 'react';
import type { AgentCategory, AgentProvider } from '../../../types/types';
import AgentCategoryContentSection from './sections/AgentCategoryContentSection';
import AgentCategoryTabsSection from './sections/AgentCategoryTabsSection';
import AgentSelectorSection from './sections/AgentSelectorSection';
import type { AgentContext, AgentsSettingsTabProps } from './types';

export default function AgentsSettingsTab({
  claudeAuthStatus,
  codexAuthStatus,
  onClaudeLogin,
  onCodexLogin,
  claudeAccountSettings,
  onClaudeAccountSettingsChange,
  claudePermissions,
  onClaudePermissionsChange,
  codexAccountSettings,
  onCodexAccountSettingsChange,
  codexPermissionMode,
  onCodexPermissionModeChange,
  mcpServers,
  codexMcpServers,
  mcpTestResults,
  mcpServerTools,
  mcpToolsLoading,
  deleteError,
  onOpenMcpForm,
  onDeleteMcpServer,
  onTestMcpServer,
  onDiscoverMcpTools,
  onOpenCodexMcpForm,
  onDeleteCodexMcpServer,
}: AgentsSettingsTabProps) {
  const [selectedAgent, setSelectedAgent] = useState<AgentProvider>('claude');
  const [selectedCategory, setSelectedCategory] = useState<AgentCategory>('account');

  const agentContextById = useMemo<Record<AgentProvider, AgentContext>>(() => ({
    claude: {
      authStatus: claudeAuthStatus,
      onLogin: onClaudeLogin,
    },
    codex: {
      authStatus: codexAuthStatus,
      onLogin: onCodexLogin,
    },
  }), [
    claudeAuthStatus,
    codexAuthStatus,
    onClaudeLogin,
    onCodexLogin,
  ]);

  return (
    <div className="-mx-4 -mb-4 -mt-2 flex min-h-[300px] flex-col overflow-hidden md:-mx-6 md:-mb-6 md:-mt-2 md:min-h-[500px]">
      <AgentSelectorSection
        selectedAgent={selectedAgent}
        onSelectAgent={setSelectedAgent}
        agentContextById={agentContextById}
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <AgentCategoryTabsSection
          selectedCategory={selectedCategory}
          onSelectCategory={setSelectedCategory}
        />

        <AgentCategoryContentSection
          selectedAgent={selectedAgent}
          selectedCategory={selectedCategory}
          agentContextById={agentContextById}
          claudeAccountSettings={claudeAccountSettings}
          onClaudeAccountSettingsChange={onClaudeAccountSettingsChange}
          claudePermissions={claudePermissions}
          onClaudePermissionsChange={onClaudePermissionsChange}
          codexAccountSettings={codexAccountSettings}
          onCodexAccountSettingsChange={onCodexAccountSettingsChange}
          codexPermissionMode={codexPermissionMode}
          onCodexPermissionModeChange={onCodexPermissionModeChange}
          mcpServers={mcpServers}
          codexMcpServers={codexMcpServers}
          mcpTestResults={mcpTestResults}
          mcpServerTools={mcpServerTools}
          mcpToolsLoading={mcpToolsLoading}
          deleteError={deleteError}
          onOpenMcpForm={onOpenMcpForm}
          onDeleteMcpServer={onDeleteMcpServer}
          onTestMcpServer={onTestMcpServer}
          onDiscoverMcpTools={onDiscoverMcpTools}
          onOpenCodexMcpForm={onOpenCodexMcpForm}
          onDeleteCodexMcpServer={onDeleteCodexMcpServer}
        />
      </div>
    </div>
  );
}
