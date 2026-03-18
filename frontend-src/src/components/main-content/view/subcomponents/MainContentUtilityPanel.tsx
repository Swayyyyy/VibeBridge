import { Folder, GitBranch, Terminal, X, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import FileTree from '../../../file-tree/view/FileTree';
import GitPanel from '../../../git-panel/view/GitPanel';
import StandaloneShell from '../../../standalone-shell/view/StandaloneShell';
import type { Project, ProjectSession, UtilityPanelTab } from '../../../../types/app';

type MainContentUtilityPanelProps = {
  panelTab: UtilityPanelTab | null;
  selectedProject: Project;
  selectedSession: ProjectSession | null;
  isMobile: boolean;
  onClose: () => void;
  onFileOpen: (filePath: string) => void;
};

type PanelConfig = {
  icon: LucideIcon;
  labelKey: 'tabs.shell' | 'tabs.files' | 'tabs.git';
};

const PANEL_CONFIG: Record<UtilityPanelTab, PanelConfig> = {
  shell: { icon: Terminal, labelKey: 'tabs.shell' },
  files: { icon: Folder, labelKey: 'tabs.files' },
  git: { icon: GitBranch, labelKey: 'tabs.git' },
};

export default function MainContentUtilityPanel({
  panelTab,
  selectedProject,
  selectedSession,
  isMobile,
  onClose,
  onFileOpen,
}: MainContentUtilityPanelProps) {
  const { t } = useTranslation('common');

  if (!panelTab) {
    return null;
  }

  const { icon: Icon, labelKey } = PANEL_CONFIG[panelTab];
  const panelTitle = t(labelKey);

  let content = null;
  if (panelTab === 'files') {
    content = <FileTree selectedProject={selectedProject} onFileOpen={onFileOpen} />;
  } else if (panelTab === 'shell') {
    content = (
      <StandaloneShell
        project={selectedProject}
        session={selectedSession}
        showHeader={false}
        isActive
      />
    );
  } else if (panelTab === 'git') {
    content = (
      <GitPanel
        selectedProject={selectedProject}
        isMobile={isMobile}
        onFileOpen={onFileOpen}
      />
    );
  }

  const header = (
    <div className="flex h-12 flex-shrink-0 items-center justify-between border-b border-border/60 px-4">
      <div className="flex min-w-0 items-center gap-2">
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-muted/60">
          <Icon className="h-4 w-4 text-foreground" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{panelTitle}</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {selectedProject.displayName}
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={onClose}
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent/80 hover:text-foreground"
        aria-label={t('buttons.close')}
        title={t('buttons.close')}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );

  if (isMobile) {
    return (
      <>
        <button
          type="button"
          className="fixed inset-0 z-[60] bg-background/60 backdrop-blur-sm"
          onClick={onClose}
          aria-label={t('buttons.close')}
        />
        <aside className="fixed inset-y-0 right-0 z-[70] flex h-full w-[min(88vw,28rem)] flex-col border-l border-border bg-background shadow-2xl">
          {header}
          <div className="min-h-0 flex-1 overflow-hidden">{content}</div>
        </aside>
      </>
    );
  }

  return (
    <aside className="flex h-full w-[360px] min-w-[320px] max-w-[420px] flex-shrink-0 flex-col border-l border-border/60 bg-background/95 shadow-2xl xl:w-[400px]">
      {header}
      <div className="min-h-0 flex-1 overflow-hidden">{content}</div>
    </aside>
  );
}
