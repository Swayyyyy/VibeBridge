import { useTranslation } from 'react-i18next';
import { useOptionalNodes } from '../../../../contexts/NodeContext';
import SessionProviderLogo from '../../../llm-logo-provider/SessionProviderLogo';
import type { MainAreaTab, Project, ProjectSession } from '../../../../types/app';
import { usePlugins } from '../../../../contexts/PluginsContext';
import { isNodeOffline } from '../../../../utils/nodeStatus';

type MainContentTitleProps = {
  mainTab: MainAreaTab;
  selectedProject: Project;
  selectedSession: ProjectSession | null;
  shouldShowTasksTab: boolean;
};

function getTabTitle(mainTab: MainAreaTab, shouldShowTasksTab: boolean, t: (key: string) => string, pluginDisplayName?: string) {
  if (mainTab.startsWith('plugin:') && pluginDisplayName) {
    return pluginDisplayName;
  }

  if (mainTab === 'tasks' && shouldShowTasksTab) {
    return 'TaskMaster';
  }

  return t('mainContent.workspace');
}

function getSessionTitle(session: ProjectSession, t: (key: string) => string): string {
  if (session.__provider === 'codex') {
    return (session.summary as string) || (session.name as string) || t('sidebar:projects.codexSession');
  }

  return (session.summary as string) || (session.name as string) || t('mainContent.newSession');
}

function getProjectSubtitle(selectedProject: Project): string {
  const base = selectedProject.displayName;
  if (selectedProject.nodeDisplayName && selectedProject.nodeDisplayName.trim().length > 0) {
    return `${selectedProject.nodeDisplayName} · ${base}`;
  }
  return base;
}

export default function MainContentTitle({
  mainTab,
  selectedProject,
  selectedSession,
  shouldShowTasksTab,
}: MainContentTitleProps) {
  const { t } = useTranslation(['common', 'sidebar']);
  const { plugins } = usePlugins();
  const nodeContext = useOptionalNodes();

  const pluginDisplayName = mainTab.startsWith('plugin:')
    ? plugins.find((p) => p.name === mainTab.replace('plugin:', ''))?.displayName
    : undefined;
  const currentNodeId = selectedProject.nodeId ?? selectedSession?.__nodeId ?? null;
  const showOfflineBadge =
    mainTab === 'chat' && isNodeOffline(nodeContext?.nodes ?? [], currentNodeId);

  const showSessionIcon = mainTab === 'chat' && Boolean(selectedSession);
  const showChatNewSession = mainTab === 'chat' && !selectedSession;

  return (
    <div className="scrollbar-hide flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
      {showSessionIcon && (
        <div className="flex h-5 w-5 flex-shrink-0 items-center justify-center">
          <SessionProviderLogo provider={selectedSession?.__provider} className="h-4 w-4" />
        </div>
      )}

      <div className="min-w-0 flex-1">
        {mainTab === 'chat' && selectedSession ? (
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <h2 className="scrollbar-hide min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-sm font-semibold leading-tight text-foreground">
                {getSessionTitle(selectedSession, t)}
              </h2>
              {showOfflineBadge && (
                <span className="rounded-md border border-red-500/25 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-600 dark:text-red-300">
                  {t('sidebar:workspaceSessions.offlineBadge')}
                </span>
              )}
            </div>
            <div className="truncate text-[11px] leading-tight text-muted-foreground">{getProjectSubtitle(selectedProject)}</div>
          </div>
        ) : showChatNewSession ? (
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <h2 className="min-w-0 flex-1 truncate text-base font-semibold leading-tight text-foreground">
                {t('mainContent.newSession')}
              </h2>
              {showOfflineBadge && (
                <span className="rounded-md border border-red-500/25 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-600 dark:text-red-300">
                  {t('sidebar:workspaceSessions.offlineBadge')}
                </span>
              )}
            </div>
            <div className="truncate text-xs leading-tight text-muted-foreground">{getProjectSubtitle(selectedProject)}</div>
          </div>
        ) : (
          <div className="min-w-0">
            <h2 className="text-sm font-semibold leading-tight text-foreground">
              {getTabTitle(mainTab, shouldShowTasksTab, t, pluginDisplayName)}
            </h2>
            <div className="truncate text-[11px] leading-tight text-muted-foreground">{getProjectSubtitle(selectedProject)}</div>
          </div>
        )}
      </div>
    </div>
  );
}
