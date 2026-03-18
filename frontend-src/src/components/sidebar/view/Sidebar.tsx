import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronDown,
  ChevronRight,
  Folder,
  MessageSquarePlus,
  PanelLeftClose,
  RefreshCw,
  Search,
  Server,
  X,
} from 'lucide-react';
import { IS_PLATFORM } from '../../../constants/config';
import { useNodes } from '../../../contexts/NodeContext';
import { useDeviceSettings } from '../../../hooks/useDeviceSettings';
import { useUiPreferences } from '../../../hooks/useUiPreferences';
import { useVersionCheck } from '../../../hooks/useVersionCheck';
import type { OpenSessionEntry, Project, SessionProvider } from '../../../types/app';
import { api, prefixUrl } from '../../../utils/api';
import { isNodeOffline } from '../../../utils/nodeStatus';
import { Button, Input, ScrollArea } from '../../../shared/view/ui';
import SessionProviderLogo from '../../llm-logo-provider/SessionProviderLogo';
import type { SidebarProps } from '../types/types';
import SidebarCollapsed from './subcomponents/SidebarCollapsed';
import SidebarFooter from './subcomponents/SidebarFooter';
import SidebarModals from './subcomponents/SidebarModals';

type PickerMode = 'new' | 'existing' | null;
type SidebarSessionViewMode = 'flat' | 'grouped';
type TranslateFn = (key: string, options?: Record<string, unknown>) => string;
type WorkspacePathStatusTone = 'default' | 'success' | 'error';

type FilesystemSuggestion = {
  path: string;
  name: string;
  type?: string;
};

type WorkspacePathStatus = {
  tone: WorkspacePathStatusTone;
  message: string;
};

type BrowseFilesystemPayload = {
  path?: string;
  suggestions?: FilesystemSuggestion[];
  detail?: string;
  error?: string;
};

type SessionTreeProjectGroup = {
  key: string;
  projectDisplayName: string;
  projectPath: string;
  sessions: OpenSessionEntry[];
};

type SessionTreeNodeGroup = {
  key: string;
  nodeLabel: string;
  sessionCount: number;
  projects: SessionTreeProjectGroup[];
};

const SIDEBAR_SESSION_VIEW_MODE_STORAGE_KEY = 'sidebar-session-view-mode-v1';
const MAX_NEW_SESSION_PATH_SUGGESTIONS = 8;

const matchesSessionSearch = (entry: OpenSessionEntry, query: string) => {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return true;
  }

  return [
    entry.title,
    entry.projectDisplayName,
    entry.projectPath,
    entry.nodeDisplayName,
    entry.provider,
    entry.sessionId,
  ]
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
    .some((value) => value.toLowerCase().includes(normalizedQuery));
};

const getProviderLabel = (provider?: SessionProvider) => {
  return provider === 'codex' ? 'Codex' : 'Claude';
};

const isDefaultFallbackTitle = (title: string) => {
  return title === 'New Session' || title === 'Codex Session';
};

const getDisplaySessionTitle = (entry: OpenSessionEntry, t: TranslateFn) => {
  const normalizedTitle = entry.title?.trim();
  if (!normalizedTitle || isDefaultFallbackTitle(normalizedTitle)) {
    if (!entry.isDraft && entry.provider === 'codex') {
      return t('projects.codexSession');
    }
    return t('sessions.newSession');
  }

  return normalizedTitle;
};

const getPathLeafName = (projectPath: string) => {
  const normalized = projectPath.replace(/\\/g, '/').replace(/\/+$/, '');
  const segments = normalized.split('/').filter(Boolean);
  return segments[segments.length - 1] || normalized || projectPath;
};

const getProjectTreeTitle = (projectPath: string, projectDisplayName: string) => {
  return getPathLeafName(projectPath) || projectDisplayName || projectPath;
};

const readPersistedSessionViewMode = (): SidebarSessionViewMode => {
  try {
    const stored = localStorage.getItem(SIDEBAR_SESSION_VIEW_MODE_STORAGE_KEY);
    return stored === 'grouped' ? 'grouped' : 'flat';
  } catch {
    return 'flat';
  }
};

const extractApiErrorMessage = (payload: unknown) => {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const detail = Reflect.get(payload, 'detail');
  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim();
  }

  const error = Reflect.get(payload, 'error');
  if (typeof error === 'string' && error.trim()) {
    return error.trim();
  }

  return null;
};

const isMissingDirectoryMessage = (message: string | null) => {
  if (!message) {
    return false;
  }

  const normalized = message.toLowerCase();
  return normalized.includes('does not exist') || normalized.includes('not a directory');
};

const trimTrailingPathSeparators = (value: string) => {
  if (!value) {
    return value;
  }

  if (value === '/' || value === '\\' || value === '~' || value === '~/') {
    return value === '~/' ? '~' : value;
  }

  if (/^[A-Za-z]:[\\/]*$/.test(value)) {
    return `${value.slice(0, 2)}\\`;
  }

  return value.replace(/[\\/]+$/, '');
};

const getPathSuggestionTarget = (value: string) => {
  const trimmed = value.trim();
  if (!trimmed) {
    return { browsePath: null as string | null, filter: '' };
  }

  const lastSeparator = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  if (lastSeparator === -1) {
    return { browsePath: null as string | null, filter: trimmed };
  }

  const basePath = trimTrailingPathSeparators(trimmed.slice(0, lastSeparator + 1));
  const browsePath = basePath || (trimmed.startsWith('\\') ? '\\' : '/');
  const filter = trimmed.slice(lastSeparator + 1);
  return { browsePath, filter };
};

const normalizePathSuggestions = (suggestions: unknown): FilesystemSuggestion[] => {
  if (!Array.isArray(suggestions)) {
    return [];
  }

  return suggestions
    .filter((suggestion): suggestion is FilesystemSuggestion => {
      return (
        Boolean(suggestion) &&
        typeof suggestion === 'object' &&
        typeof suggestion.path === 'string' &&
        typeof suggestion.name === 'string'
      );
    })
    .slice(0, MAX_NEW_SESSION_PATH_SUGGESTIONS);
};

const filterPathSuggestions = (suggestions: FilesystemSuggestion[], filter: string) => {
  const normalizedFilter = filter.trim().toLowerCase();
  if (!normalizedFilter) {
    return suggestions.slice(0, MAX_NEW_SESSION_PATH_SUGGESTIONS);
  }

  return suggestions
    .filter((suggestion) => suggestion.name.toLowerCase().includes(normalizedFilter))
    .slice(0, MAX_NEW_SESSION_PATH_SUGGESTIONS);
};

const getWorkspacePathStatusClassName = (tone: WorkspacePathStatusTone) => {
  if (tone === 'success') {
    return 'text-emerald-600 dark:text-emerald-300';
  }

  if (tone === 'error') {
    return 'text-red-600 dark:text-red-300';
  }

  return 'text-muted-foreground';
};

const normalizeCreatedProject = (
  project: Partial<Project> | null | undefined,
  projectPath: string,
  nodeId: string | null,
  nodeDisplayName: string | null,
): Project => {
  const resolvedPath = project?.fullPath || project?.path || projectPath;
  const resolvedName = project?.name || getPathLeafName(resolvedPath);
  const resolvedDisplayName = project?.displayName || resolvedName;

  return {
    ...(project || {}),
    name: resolvedName,
    displayName: resolvedDisplayName,
    fullPath: resolvedPath,
    path: project?.path || resolvedPath,
    nodeId,
    nodeDisplayName,
    sessions: Array.isArray(project?.sessions) ? project.sessions : [],
    codexSessions: Array.isArray(project?.codexSessions) ? project.codexSessions : [],
    sessionMeta:
      project?.sessionMeta && typeof project.sessionMeta === 'object'
        ? project.sessionMeta
        : { hasMore: false, total: 0 },
  };
};

const resolvePreferredNodeId = (
  nodes: Array<{ nodeId: string; status: string }>,
  selectedNodeId: string | null,
) => {
  if (selectedNodeId && nodes.some((node) => node.nodeId === selectedNodeId)) {
    return selectedNodeId;
  }

  return nodes.find((node) => node.status === 'online')?.nodeId || nodes[0]?.nodeId || '';
};

const buildSessionTree = (
  entries: OpenSessionEntry[],
  t: TranslateFn,
): SessionTreeNodeGroup[] => {
  const nodeMap = new Map<
    string,
    SessionTreeNodeGroup & { projectMap: Map<string, SessionTreeProjectGroup> }
  >();

  entries.forEach((entry) => {
    const nodeKey = entry.nodeId || '__local__';
    const projectIdentity = entry.projectPath || entry.projectName || entry.projectDisplayName;
    const projectKey = `${nodeKey}:${projectIdentity}`;

    let nodeGroup = nodeMap.get(nodeKey);
    if (!nodeGroup) {
      nodeGroup = {
        key: nodeKey,
        nodeLabel: entry.nodeDisplayName || t('workspaceSessions.localNode'),
        sessionCount: 0,
        projects: [],
        projectMap: new Map(),
      };
      nodeMap.set(nodeKey, nodeGroup);
    }

    nodeGroup.sessionCount += 1;

    let projectGroup = nodeGroup.projectMap.get(projectKey);
    if (!projectGroup) {
      projectGroup = {
        key: projectKey,
        projectDisplayName: entry.projectDisplayName,
        projectPath: entry.projectPath,
        sessions: [],
      };
      nodeGroup.projectMap.set(projectKey, projectGroup);
      nodeGroup.projects.push(projectGroup);
    }

    projectGroup.sessions.push(entry);
  });

  return Array.from(nodeMap.values()).map(({ projectMap: _projectMap, ...nodeGroup }) => nodeGroup);
};

const collectExpandedTreeKeys = (tree: SessionTreeNodeGroup[]) => {
  return tree.flatMap((nodeGroup) => [nodeGroup.key, ...nodeGroup.projects.map((projectGroup) => projectGroup.key)]);
};

function SidebarLogo({ title }: { title: string }) {
  return (
    <div className="flex h-[3.6rem] w-[3.6rem] flex-shrink-0 items-center justify-center rounded-[0.9rem] border border-border/40 bg-card/70 p-1 shadow-sm">
      <img
        src={prefixUrl('/logo-64.png')}
        alt={`${title} logo`}
        className="h-full w-full scale-[1.08] object-contain"
      />
    </div>
  );
}

function OfflineBadge() {
  const { t } = useTranslation('sidebar');

  return (
    <span className="rounded-md border border-red-500/25 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-600 dark:text-red-300">
      {t('workspaceSessions.offlineBadge')}
    </span>
  );
}

function HeaderActionButton({
  title,
  icon,
  onClick,
  disabled = false,
}: {
  title: string;
  icon: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/45 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
    >
      {icon}
    </button>
  );
}

function ConnectedNodesSummary({
  nodes,
  onRemoveNode,
}: {
  nodes: Array<{ nodeId: string; displayName: string; status: string }>;
  onRemoveNode: (nodeId: string) => Promise<void>;
}) {
  const { t } = useTranslation('sidebar');
  const [isOpen, setIsOpen] = useState(false);
  const [removingNodeId, setRemovingNodeId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onlineNodes = useMemo(
    () => nodes.filter((node) => node.status === 'online'),
    [nodes],
  );
  const offlineNodes = useMemo(
    () => nodes.filter((node) => node.status !== 'online'),
    [nodes],
  );
  const sortedNodes = useMemo(() => {
    return [...nodes].sort((left, right) => {
      const leftOnline = left.status === 'online';
      const rightOnline = right.status === 'online';
      if (leftOnline !== rightOnline) {
        return leftOnline ? -1 : 1;
      }
      return (left.displayName || left.nodeId).localeCompare(right.displayName || right.nodeId);
    });
  }, [nodes]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (containerRef.current && target instanceof Node && !containerRef.current.contains(target)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [isOpen]);

  const handleRemoveNode = async (nodeId: string, displayName: string) => {
    const confirmed = window.confirm(
      t('workspaceSessions.removeNodeConfirm', {
        node: displayName,
      }),
    );
    if (!confirmed) {
      return;
    }

    setRemovingNodeId(nodeId);
    try {
      await onRemoveNode(nodeId);
    } catch (error) {
      window.alert(
        error instanceof Error ? error.message : t('workspaceSessions.removeNodeFailed'),
      );
    } finally {
      setRemovingNodeId(null);
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        className="inline-flex w-full items-center gap-1.5 rounded-xl border border-border/60 bg-card/70 px-2.5 py-0.5 text-left transition-colors hover:bg-accent/60"
        onClick={() => setIsOpen((previous) => !previous)}
        aria-expanded={isOpen}
      >
        <Server className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-foreground">
          {t('workspaceSessions.onlineCount', { count: onlineNodes.length })}
        </span>
        {offlineNodes.length > 0 && (
          <span className="rounded-md border border-red-500/25 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-red-600 dark:text-red-300">
            {t('workspaceSessions.offlineCount', { count: offlineNodes.length })}
          </span>
        )}
        <ChevronDown
          className={`h-3.5 w-3.5 flex-shrink-0 text-muted-foreground transition-transform ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {isOpen && (
        <div className="absolute left-0 right-0 top-full z-20 mt-2 rounded-2xl border border-border/70 bg-background/95 p-2 shadow-lg backdrop-blur">
          {sortedNodes.length === 0 ? (
            <div className="px-2 py-3 text-sm text-muted-foreground">
              {t('workspaceSessions.nodesEmpty')}
            </div>
          ) : (
            <div className="space-y-1">
              {sortedNodes.map((node) => {
                const showSecondaryLine = node.displayName !== node.nodeId;
                const isOffline = node.status !== 'online';
                const isRemoving = removingNodeId === node.nodeId;

                return (
                  <div
                    key={node.nodeId}
                    className={`rounded-xl border px-3 py-2 ${
                      isOffline
                        ? 'border-border/60 bg-muted/35'
                        : 'border-border/60 bg-card/70'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className={`truncate text-sm font-medium ${isOffline ? 'text-foreground/75' : 'text-foreground'}`}>
                          {node.displayName}
                        </div>
                        {showSecondaryLine && (
                          <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                            {node.nodeId}
                          </div>
                        )}
                      </div>
                      <div className="flex flex-shrink-0 items-center gap-2">
                        {isOffline ? (
                          <OfflineBadge />
                        ) : (
                          <span className="rounded-md border border-border/60 bg-background/80 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                            {t('workspaceSessions.onlineLabel')}
                          </span>
                        )}
                        {isOffline && (
                          <button
                            type="button"
                            className="rounded-md border border-border/60 bg-background/80 px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
                            onClick={() => void handleRemoveNode(node.nodeId, node.displayName)}
                            disabled={isRemoving}
                            aria-label={t('workspaceSessions.removeNodeAria', {
                              node: node.displayName,
                            })}
                          >
                            {isRemoving
                              ? t('workspaceSessions.removingNode')
                              : t('workspaceSessions.removeNode')}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SidebarHeader({
  title,
  nodes,
  isRefreshing,
  onRefresh,
  onCollapse,
  onRemoveNode,
}: {
  title: string;
  nodes: Array<{ nodeId: string; displayName: string; status: string }>;
  isRefreshing: boolean;
  onRefresh: () => void;
  onCollapse: () => void;
  onRemoveNode: (nodeId: string) => Promise<void>;
}) {
  const { t } = useTranslation('sidebar');
  const logo = <SidebarLogo title={title} />;

  return (
    <div className="flex items-center gap-2">
      {IS_PLATFORM ? (
        <a
          href="https://cloudcli.ai/dashboard"
          className="flex flex-shrink-0 transition-opacity hover:opacity-80 active:opacity-70"
          title={t('tooltips.viewEnvironments')}
        >
          {logo}
        </a>
      ) : (
        logo
      )}

      <div className="min-w-0 flex flex-1 flex-col justify-center">
        <div className="flex min-h-6 items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[13px] font-semibold leading-none tracking-[-0.02em] text-foreground">
              {title}
            </h1>
          </div>

          <div className="flex flex-shrink-0 items-center justify-end gap-0.5">
            <HeaderActionButton
              title={t('tooltips.refresh')}
              onClick={onRefresh}
              disabled={isRefreshing}
              icon={<RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />}
            />
            <HeaderActionButton
              title={t('tooltips.hideSidebar')}
              onClick={onCollapse}
              icon={<PanelLeftClose className="h-3.5 w-3.5" />}
            />
          </div>
        </div>

        <div className="mt-0.5">
          <ConnectedNodesSummary nodes={nodes} onRemoveNode={onRemoveNode} />
        </div>
      </div>
    </div>
  );
}

function ActionCardButton({
  label,
  onClick,
  icon,
}: {
  label: string;
  onClick: () => void;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-w-0 flex-1 items-center justify-center gap-2 rounded-xl border border-border/60 bg-card/70 px-3 py-3 text-center transition-colors hover:bg-accent/60"
    >
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-muted/55 text-foreground">
        {icon}
      </div>
      <div className="truncate text-sm font-semibold tracking-[-0.01em] text-foreground">
        {label}
      </div>
    </button>
  );
}

function SessionBadges({ entry }: { entry: OpenSessionEntry }) {
  const { t } = useTranslation('sidebar');

  return (
    <>
      {entry.isDraft && (
        <span className="rounded-md border border-border/60 bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {t('workspaceSessions.draftBadge')}
        </span>
      )}
      {entry.provider && (
        <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background/80 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          <SessionProviderLogo provider={entry.provider} className="h-3 w-3" />
          {getProviderLabel(entry.provider)}
        </span>
      )}
    </>
  );
}

function SessionViewModeToggle({
  value,
  onChange,
}: {
  value: SidebarSessionViewMode;
  onChange: (value: SidebarSessionViewMode) => void;
}) {
  const { t } = useTranslation('sidebar');

  return (
    <div className="inline-flex rounded-lg border border-border/60 bg-muted/30 p-0.5">
      {(['flat', 'grouped'] as SidebarSessionViewMode[]).map((mode) => {
        const isActive = value === mode;
        return (
          <button
            key={mode}
            type="button"
            onClick={() => onChange(mode)}
            className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
              isActive
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {mode === 'flat'
              ? t('workspaceSessions.viewModes.flat')
              : t('workspaceSessions.viewModes.grouped')}
          </button>
        );
      })}
    </div>
  );
}

function TreeCountBadge({ count }: { count: number }) {
  return (
    <span className="rounded-md border border-border/60 bg-background/80 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      {count}
    </span>
  );
}

function OpenSessionItem({
  entry,
  isActive,
  onOpen,
  onClose,
}: {
  entry: OpenSessionEntry;
  isActive: boolean;
  onOpen: (entry: OpenSessionEntry) => void;
  onClose: (key: string) => void;
}) {
  const { t } = useTranslation('sidebar');
  const { nodes } = useNodes();
  const title = getDisplaySessionTitle(entry, t);
  const nodeLabel = entry.nodeDisplayName || t('workspaceSessions.localNode');
  const entryOffline = isNodeOffline(nodes, entry.nodeId ?? null);
  const containerClasses = isActive
    ? entryOffline
      ? 'border-red-500/30 bg-red-500/6 shadow-sm'
      : 'border-primary/40 bg-primary/8 shadow-sm'
    : entryOffline
      ? 'border-border/40 bg-muted/35 hover:border-red-500/20 hover:bg-muted/45'
      : 'border-border/50 bg-card/60 hover:border-border hover:bg-accent/50';

  return (
    <div
      className={`group rounded-xl border transition-colors ${containerClasses}`}
    >
      <div className="flex min-w-0 items-start gap-3 px-3 py-3">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-background/80">
          <SessionProviderLogo provider={entry.provider || 'claude'} className="h-4 w-4" />
        </div>

        <button
          type="button"
          className="min-w-0 flex-1 text-left"
          onClick={() => onOpen(entry)}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className={`truncate text-sm font-medium ${entryOffline ? 'text-foreground/75' : 'text-foreground'}`}>
                {title}
              </div>
              <div className={`mt-0.5 truncate text-[11px] ${entryOffline ? 'text-muted-foreground/85' : 'text-muted-foreground'}`}>
                {nodeLabel + ' · ' + entry.projectDisplayName}
              </div>
            </div>
            <div className="flex flex-shrink-0 items-center gap-1">
              {entryOffline && <OfflineBadge />}
              <SessionBadges entry={entry} />
            </div>
          </div>
          <div
            className={`mt-1 truncate text-[11px] ${entryOffline ? 'text-muted-foreground/70' : 'text-muted-foreground/80'}`}
            title={entry.projectPath}
          >
            {entry.projectPath}
          </div>
        </button>

        <button
          type="button"
          className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-background/80 hover:text-foreground group-hover:opacity-100"
          onClick={() => onClose(entry.key)}
          aria-label={t('workspaceSessions.closeSession')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function SessionTreeLeaf({
  entry,
  isActive = false,
  onOpen,
  onClose,
}: {
  entry: OpenSessionEntry;
  isActive?: boolean;
  onOpen: (entry: OpenSessionEntry) => void;
  onClose?: ((key: string) => void) | null;
}) {
  const { t } = useTranslation('sidebar');
  const { nodes } = useNodes();
  const title = getDisplaySessionTitle(entry, t);
  const entryOffline = isNodeOffline(nodes, entry.nodeId ?? null);
  const containerClasses = isActive
    ? entryOffline
      ? 'border-red-500/30 bg-red-500/6 shadow-sm'
      : 'border-primary/40 bg-primary/8 shadow-sm'
    : entryOffline
      ? 'border-border/40 bg-muted/30 hover:border-red-500/20 hover:bg-muted/40'
      : 'border-border/50 bg-card/50 hover:border-border hover:bg-accent/40';

  return (
    <div
      className={`group flex min-w-0 items-start gap-2 rounded-lg border px-3 py-2 transition-colors ${containerClasses}`}
    >
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-background/80">
        <SessionProviderLogo provider={entry.provider || 'claude'} className="h-3.5 w-3.5" />
      </div>

      <button
        type="button"
        className="min-w-0 flex-1 text-left"
        onClick={() => onOpen(entry)}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className={`truncate text-sm font-medium ${entryOffline ? 'text-foreground/75' : 'text-foreground'}`}>
              {title}
            </div>
            <div className={`mt-0.5 truncate text-[11px] ${entryOffline ? 'text-muted-foreground/85' : 'text-muted-foreground'}`}>
              {entry.sessionId || entry.projectDisplayName}
            </div>
          </div>
          <div className="flex flex-shrink-0 items-center gap-1">
            {entryOffline && <OfflineBadge />}
            <SessionBadges entry={entry} />
          </div>
        </div>
      </button>

      {onClose && (
        <button
          type="button"
          className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-background/80 hover:text-foreground group-hover:opacity-100"
          onClick={(event) => {
            event.stopPropagation();
            onClose(entry.key);
          }}
          aria-label={t('workspaceSessions.closeSession')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

function SessionTreeView({
  entries,
  activeSessionKey = null,
  searchValue,
  onOpenSession,
  onCloseSession = null,
  emptyLabel,
}: {
  entries: OpenSessionEntry[];
  activeSessionKey?: string | null;
  searchValue: string;
  onOpenSession: (entry: OpenSessionEntry) => void;
  onCloseSession?: ((key: string) => void) | null;
  emptyLabel: string;
}) {
  const { t } = useTranslation('sidebar');
  const { nodes } = useNodes();
  const tree = useMemo(() => buildSessionTree(entries, t), [entries, t]);
  const allExpandableKeys = useMemo(() => collectExpandedTreeKeys(tree), [tree]);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set(allExpandableKeys));
  const searchActive = searchValue.trim().length > 0;

  useEffect(() => {
    setExpandedKeys((previous) => {
      const next = new Set(previous);
      let changed = false;

      allExpandableKeys.forEach((key) => {
        if (!next.has(key)) {
          next.add(key);
          changed = true;
        }
      });

      return changed ? next : previous;
    });
  }, [allExpandableKeys]);

  const forcedExpandedKeys = useMemo(() => new Set(allExpandableKeys), [allExpandableKeys]);
  const effectiveExpandedKeys = searchActive ? forcedExpandedKeys : expandedKeys;

  const toggleExpanded = (key: string) => {
    setExpandedKeys((previous) => {
      const next = new Set(previous);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  if (tree.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border/70 px-3 py-4 text-sm text-muted-foreground">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {tree.map((nodeGroup) => {
        const nodeExpanded = effectiveExpandedKeys.has(nodeGroup.key);
        const nodeOffline = isNodeOffline(
          nodes,
          nodeGroup.key === '__local__' ? null : nodeGroup.key,
        );

        return (
          <div key={nodeGroup.key} className="space-y-1">
            <button
              type="button"
              onClick={() => toggleExpanded(nodeGroup.key)}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-accent/40"
              aria-expanded={nodeExpanded}
            >
              {nodeExpanded ? (
                <ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
              )}
              <Server className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className={`truncate text-sm font-medium ${nodeOffline ? 'text-foreground/75' : 'text-foreground'}`}>
                  {nodeGroup.nodeLabel}
                </div>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                {nodeOffline && <OfflineBadge />}
                <TreeCountBadge count={nodeGroup.sessionCount} />
              </div>
            </button>

            {nodeExpanded && (
              <div className="space-y-1 pl-5">
                {nodeGroup.projects.map((projectGroup) => {
                  const projectExpanded = effectiveExpandedKeys.has(projectGroup.key);
                  const projectTitle = getProjectTreeTitle(
                    projectGroup.projectPath,
                    projectGroup.projectDisplayName,
                  );

                  return (
                    <div key={projectGroup.key} className="space-y-1">
                      <button
                        type="button"
                        onClick={() => toggleExpanded(projectGroup.key)}
                        className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-accent/30"
                        aria-expanded={projectExpanded}
                      >
                        {projectExpanded ? (
                          <ChevronDown className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
                        )}
                        <Folder className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-foreground">{projectTitle}</div>
                          <div
                            className="mt-0.5 truncate text-[11px] text-muted-foreground"
                            title={projectGroup.projectPath}
                          >
                            {projectGroup.projectPath}
                          </div>
                        </div>
                        <TreeCountBadge count={projectGroup.sessions.length} />
                      </button>

                      {projectExpanded && (
                        <div className="space-y-1.5 pl-6">
                          {projectGroup.sessions.map((entry) => (
                            <SessionTreeLeaf
                              key={entry.key}
                              entry={entry}
                              isActive={activeSessionKey === entry.key}
                              onOpen={onOpenSession}
                              onClose={onCloseSession}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function NewSessionPanel({
  nodes,
  nodeId,
  provider,
  path,
  pathStatus,
  pathSuggestions,
  isCheckingPath,
  isSubmitting,
  error,
  onNodeChange,
  onProviderChange,
  onPathChange,
  onSelectSuggestion,
  onSubmit,
  onClose,
}: {
  nodes: Array<{ nodeId: string; displayName: string; status: string }>;
  nodeId: string;
  provider: SessionProvider;
  path: string;
  pathStatus: WorkspacePathStatus | null;
  pathSuggestions: FilesystemSuggestion[];
  isCheckingPath: boolean;
  isSubmitting: boolean;
  error: string | null;
  onNodeChange: (nodeId: string) => void;
  onProviderChange: (provider: SessionProvider) => void;
  onPathChange: (value: string) => void;
  onSelectSuggestion: (path: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation('sidebar');
  const showNodeSelect = nodes.length > 0;
  const pathMessage = isCheckingPath
    ? t('workspaceSessions.form.pathChecking')
    : pathStatus?.message || t('workspaceSessions.form.pathHelp');
  const pathTone = isCheckingPath ? 'default' : pathStatus?.tone || 'default';

  return (
    <div className="mx-3 mb-3 rounded-2xl border border-border/60 bg-card/80 p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{t('workspaceSessions.newPanelTitle')}</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t('workspaceSessions.newPanelDescription')}
          </p>
        </div>
        <button
          type="button"
          className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onClick={onClose}
          aria-label={t('workspaceSessions.closePanel')}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-4 space-y-3">
        {showNodeSelect ? (
          <label className="block">
            <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              {t('workspaceSessions.form.nodeLabel')}
            </span>
            <select
              value={nodeId}
              onChange={(event) => onNodeChange(event.target.value)}
              className="h-10 w-full rounded-xl border border-border/60 bg-background px-3 text-sm text-foreground outline-none transition-colors focus:border-primary/50"
            >
              {nodes.map((node) => (
                <option key={node.nodeId} value={node.nodeId}>
                  {node.displayName}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className="rounded-xl border border-border/60 bg-background/70 px-3 py-2.5">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              {t('workspaceSessions.form.nodeLabel')}
            </div>
            <div className="mt-1 text-sm text-foreground">{t('workspaceSessions.localNode')}</div>
          </div>
        )}

        <div>
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {t('workspaceSessions.form.providerLabel')}
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(['claude', 'codex'] as SessionProvider[]).map((providerOption) => {
              const isActive = provider === providerOption;
              return (
                <button
                  key={providerOption}
                  type="button"
                  onClick={() => onProviderChange(providerOption)}
                  className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? 'border-primary/50 bg-primary/8 text-foreground'
                      : 'border-border/60 bg-background/70 text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                  }`}
                >
                  <SessionProviderLogo provider={providerOption} className="h-4 w-4" />
                  <span>{getProviderLabel(providerOption)}</span>
                </button>
              );
            })}
          </div>
        </div>

        <label className="block">
          <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {t('workspaceSessions.form.pathLabel')}
          </span>
          <Input
            type="text"
            value={path}
            onChange={(event) => onPathChange(event.target.value)}
            placeholder={t('workspaceSessions.form.pathPlaceholder')}
            className="h-10 rounded-xl border border-border/60 bg-background px-3 text-sm"
          />
          <span
            className={`mt-1.5 block text-[11px] leading-relaxed ${getWorkspacePathStatusClassName(pathTone)}`}
          >
            {pathMessage}
          </span>
          {pathSuggestions.length > 0 && (
            <div className="mt-2 overflow-hidden rounded-xl border border-border/60 bg-background/80">
              <div className="max-h-44 overflow-y-auto py-1">
                {pathSuggestions.map((suggestion) => (
                  <button
                    key={suggestion.path}
                    type="button"
                    className="flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-accent/55"
                    onClick={() => onSelectSuggestion(suggestion.path)}
                    title={suggestion.path}
                  >
                    <Folder className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-foreground">{suggestion.name}</span>
                      <span className="block truncate text-[11px] text-muted-foreground">{suggestion.path}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </label>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={isSubmitting}>
            {t('actions.cancel')}
          </Button>
          <Button onClick={onSubmit} disabled={isSubmitting}>
            {isSubmitting
              ? t('workspaceSessions.form.submitting')
              : t('workspaceSessions.form.submit')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function OpenSessionPickerPanel({
  searchValue,
  onSearchChange,
  onClose,
  sessions,
  onOpenSession,
}: {
  searchValue: string;
  onSearchChange: (value: string) => void;
  onClose: () => void;
  sessions: OpenSessionEntry[];
  onOpenSession: (session: OpenSessionEntry) => void;
}) {
  const { t } = useTranslation('sidebar');

  return (
    <div className="mx-3 mb-3 rounded-2xl border border-border/60 bg-card/80 p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{t('workspaceSessions.openPanelTitle')}</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t('workspaceSessions.openPanelDescription')}
          </p>
        </div>
        <button
          type="button"
          className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onClick={onClose}
          aria-label={t('workspaceSessions.closePanel')}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="relative mt-3">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/50" />
        <Input
          type="text"
          value={searchValue}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t('workspaceSessions.searchExistingPlaceholder')}
          className="h-9 rounded-xl border-0 pl-9 pr-8 text-sm focus-visible:ring-0 focus-visible:ring-offset-0"
        />
      </div>

      <div className="mt-3 max-h-72 overflow-y-auto pr-1">
        <SessionTreeView
          entries={sessions}
          searchValue={searchValue}
          onOpenSession={onOpenSession}
          emptyLabel={t('workspaceSessions.openPanelEmpty')}
        />
      </div>
    </div>
  );
}

function EmptyOpenSessionsState() {
  const { t } = useTranslation('sidebar');

  return (
    <div className="mx-3 rounded-2xl border border-dashed border-border/70 bg-card/40 px-4 py-6 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-muted/60">
        <MessageSquarePlus className="h-5 w-5 text-muted-foreground" />
      </div>
      <h2 className="text-sm font-semibold text-foreground">{t('workspaceSessions.emptyTitle')}</h2>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
        {t('workspaceSessions.emptyDescription')}
      </p>
    </div>
  );
}

export default function Sidebar({
  projects,
  openSessions,
  activeSessionKey,
  availableSessions,
  onOpenSession,
  onCreateDraft,
  onCloseSession,
  isLoading,
  onRefresh,
  onShowSettings,
  showSettings,
  settingsInitialTab,
  onCloseSettings,
  isMobile,
}: SidebarProps) {
  const { t } = useTranslation(['sidebar', 'common']);
  const { nodes, selectedNodeId, removeNode } = useNodes();
  const { isPWA } = useDeviceSettings({ trackMobile: false });
  const { updateAvailable, latestVersion, currentVersion, releaseInfo, installMode } = useVersionCheck(
    'siteboon',
    'claudecodeui',
  );
  const { preferences, setPreference } = useUiPreferences();
  const { sidebarVisible } = preferences;

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sessionListViewMode, setSessionListViewMode] = useState<SidebarSessionViewMode>(
    readPersistedSessionViewMode,
  );
  const [openSessionsSearch, setOpenSessionsSearch] = useState('');
  const [pickerMode, setPickerMode] = useState<PickerMode>(null);
  const [pickerSearch, setPickerSearch] = useState('');
  const [showVersionModal, setShowVersionModal] = useState(false);
  const [newSessionNodeId, setNewSessionNodeId] = useState('');
  const [newSessionPath, setNewSessionPath] = useState('');
  const [newSessionProvider, setNewSessionProvider] = useState<SessionProvider>('claude');
  const [newSessionError, setNewSessionError] = useState<string | null>(null);
  const [newSessionPathStatus, setNewSessionPathStatus] = useState<WorkspacePathStatus | null>(null);
  const [newSessionPathSuggestions, setNewSessionPathSuggestions] = useState<FilesystemSuggestion[]>([]);
  const [newSessionResolvedPath, setNewSessionResolvedPath] = useState<string | null>(null);
  const [isCheckingNewSessionPath, setIsCheckingNewSessionPath] = useState(false);
  const [isCreatingSession, setIsCreatingSession] = useState(false);
  const newSessionPathLookupRef = useRef(0);

  const isSidebarCollapsed = !isMobile && !sidebarVisible;

  useEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }

    document.documentElement.classList.toggle('pwa-mode', isPWA);
    document.body.classList.toggle('pwa-mode', isPWA);
  }, [isPWA]);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_SESSION_VIEW_MODE_STORAGE_KEY, sessionListViewMode);
    } catch {
      // localStorage unavailable
    }
  }, [sessionListViewMode]);

  useEffect(() => {
    if (pickerMode !== 'new' || nodes.length === 0) {
      return;
    }

    setNewSessionNodeId((previous) => {
      if (previous && nodes.some((node) => node.nodeId === previous)) {
        return previous;
      }
      return resolvePreferredNodeId(nodes, selectedNodeId);
    });
  }, [nodes, pickerMode, selectedNodeId]);

  useEffect(() => {
    if (pickerMode !== 'new') {
      return;
    }

    const lookupId = newSessionPathLookupRef.current + 1;
    newSessionPathLookupRef.current = lookupId;

    const targetNodeId =
      nodes.length > 0
        ? newSessionNodeId || resolvePreferredNodeId(nodes, selectedNodeId)
        : null;
    const targetNode = targetNodeId
      ? nodes.find((node) => node.nodeId === targetNodeId) || null
      : null;

    if (targetNode && targetNode.status !== 'online') {
      setIsCheckingNewSessionPath(false);
      setNewSessionResolvedPath(null);
      setNewSessionPathSuggestions([]);
      setNewSessionPathStatus({
        tone: 'error',
        message: t('workspaceSessions.messages.nodeUnavailable', {
          node: targetNode.displayName,
        }),
      });
      return;
    }

    const timerId = window.setTimeout(async () => {
      setIsCheckingNewSessionPath(true);

      const typedPath = newSessionPath.trim();

      try {
        if (!typedPath) {
          const response = await api.browseFilesystem(null, targetNodeId);
          const payload = (await response.json().catch(() => null)) as BrowseFilesystemPayload | null;

          if (newSessionPathLookupRef.current !== lookupId) {
            return;
          }

          setNewSessionResolvedPath(null);
          setNewSessionPathSuggestions(normalizePathSuggestions(payload?.suggestions));
          setNewSessionPathStatus({
            tone: 'default',
            message: t('workspaceSessions.form.pathHelp'),
          });
          return;
        }

        const exactLookup = await api.browseFilesystem(typedPath, targetNodeId);
        const exactPayload = (await exactLookup.json().catch(() => null)) as BrowseFilesystemPayload | null;

        if (newSessionPathLookupRef.current !== lookupId) {
          return;
        }

        if (exactLookup.ok) {
          const resolvedPath = typeof exactPayload?.path === 'string' ? exactPayload.path : typedPath;
          setNewSessionResolvedPath(resolvedPath);
          setNewSessionPathSuggestions(normalizePathSuggestions(exactPayload?.suggestions));
          setNewSessionPathStatus({
            tone: 'success',
            message: t('workspaceSessions.form.pathResolved', {
              path: resolvedPath,
            }),
          });
          return;
        }

        const exactError = extractApiErrorMessage(exactPayload);
        const { browsePath, filter } = getPathSuggestionTarget(typedPath);
        const suggestionLookup = await api.browseFilesystem(browsePath, targetNodeId);
        const suggestionPayload = (await suggestionLookup.json().catch(() => null)) as BrowseFilesystemPayload | null;

        if (newSessionPathLookupRef.current !== lookupId) {
          return;
        }

        if (!suggestionLookup.ok) {
          setNewSessionResolvedPath(null);
          setNewSessionPathSuggestions([]);
          setNewSessionPathStatus({
            tone: 'error',
            message:
              exactError ||
              extractApiErrorMessage(suggestionPayload) ||
              t('workspaceSessions.messages.pathLookupFailed'),
          });
          return;
        }

        const suggestions = filterPathSuggestions(
          normalizePathSuggestions(suggestionPayload?.suggestions),
          filter,
        );

        setNewSessionResolvedPath(null);
        setNewSessionPathSuggestions(suggestions);

        if (exactError && !isMissingDirectoryMessage(exactError)) {
          setNewSessionPathStatus({
            tone: 'error',
            message: exactError,
          });
          return;
        }

        if (suggestions.length > 0) {
          setNewSessionPathStatus({
            tone: 'default',
            message: t('workspaceSessions.form.pathSuggestionHelp', {
              path:
                typeof suggestionPayload?.path === 'string'
                  ? suggestionPayload.path
                  : browsePath || '~',
            }),
          });
          return;
        }

        setNewSessionPathStatus({
          tone: 'default',
          message: t('workspaceSessions.messages.pathNotFound'),
        });
      } catch {
        if (newSessionPathLookupRef.current !== lookupId) {
          return;
        }

        setNewSessionResolvedPath(null);
        setNewSessionPathSuggestions([]);
        setNewSessionPathStatus({
          tone: 'error',
          message: t('workspaceSessions.messages.pathLookupFailed'),
        });
      } finally {
        if (newSessionPathLookupRef.current === lookupId) {
          setIsCheckingNewSessionPath(false);
        }
      }
    }, 220);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [newSessionNodeId, newSessionPath, nodes, pickerMode, selectedNodeId, t]);

  const filteredOpenSessions = useMemo(() => {
    return openSessions.filter((entry) => matchesSessionSearch(entry, openSessionsSearch));
  }, [openSessions, openSessionsSearch]);

  const filteredAvailableSessions = useMemo(() => {
    const openedIds = new Set(
      openSessions
        .map((entry) => entry.sessionId)
        .filter((sessionId): sessionId is string => typeof sessionId === 'string' && sessionId.length > 0),
    );

    return availableSessions.filter((entry) => {
      if (entry.sessionId && openedIds.has(entry.sessionId)) {
        return false;
      }
      return matchesSessionSearch(entry, pickerSearch);
    });
  }, [availableSessions, openSessions, pickerSearch]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setTimeout(() => {
        setIsRefreshing(false);
      }, 250);
    }
  };

  const openPicker = (mode: Exclude<PickerMode, null>) => {
    setPickerMode(mode);
    setPickerSearch('');

    if (mode === 'new') {
      newSessionPathLookupRef.current += 1;
      setNewSessionNodeId(resolvePreferredNodeId(nodes, selectedNodeId));
      setNewSessionPath('');
      setNewSessionProvider('claude');
      setNewSessionError(null);
      setNewSessionPathStatus(null);
      setNewSessionPathSuggestions([]);
      setNewSessionResolvedPath(null);
      setIsCheckingNewSessionPath(false);
    }
  };

  const closePicker = () => {
    newSessionPathLookupRef.current += 1;
    setPickerMode(null);
    setPickerSearch('');
    setNewSessionError(null);
    setNewSessionPathStatus(null);
    setNewSessionPathSuggestions([]);
    setNewSessionResolvedPath(null);
    setIsCheckingNewSessionPath(false);
  };

  const collapseSidebar = () => {
    setPreference('sidebarVisible', false);
  };

  const expandSidebar = () => {
    setPreference('sidebarVisible', true);
  };

  const handleCreateDraft = (project: Project, provider: SessionProvider = 'claude') => {
    closePicker();
    onCreateDraft(project, provider);
  };

  const handleOpenExistingSession = (entry: OpenSessionEntry) => {
    closePicker();
    onOpenSession(entry);
  };

  const handleNewSessionNodeChange = (value: string) => {
    setNewSessionNodeId(value);
    setNewSessionError(null);
  };

  const handleNewSessionPathChange = (value: string) => {
    setNewSessionPath(value);
    setNewSessionError(null);
  };

  const handleSelectNewSessionSuggestion = (value: string) => {
    setNewSessionPath(value);
    setNewSessionError(null);
  };

  const handleCreateSession = async () => {
    const trimmedPath = newSessionPath.trim();
    const targetNodeId =
      nodes.length > 0
        ? newSessionNodeId || resolvePreferredNodeId(nodes, selectedNodeId)
        : null;
    const targetNode = targetNodeId
      ? nodes.find((node) => node.nodeId === targetNodeId) || null
      : null;

    if (!trimmedPath) {
      setNewSessionError(t('workspaceSessions.messages.pathRequired'));
      return;
    }

    if (nodes.length > 0 && !targetNodeId) {
      setNewSessionError(t('workspaceSessions.messages.nodeRequired'));
      return;
    }

    if (targetNode && targetNode.status !== 'online') {
      setNewSessionError(
        t('workspaceSessions.messages.nodeUnavailable', {
          node: targetNode.displayName,
        }),
      );
      return;
    }

    setIsCreatingSession(true);
    setNewSessionError(null);

    try {
      const validationResponse = await api.browseFilesystem(trimmedPath, targetNodeId);
      const validationPayload = (await validationResponse.json().catch(() => null)) as BrowseFilesystemPayload | null;

      if (!validationResponse.ok) {
        throw new Error(
          extractApiErrorMessage(validationPayload) ||
            t('workspaceSessions.messages.pathNotFound'),
        );
      }

      const resolvedPath =
        typeof validationPayload?.path === 'string'
          ? validationPayload.path
          : newSessionResolvedPath || trimmedPath;

      setNewSessionResolvedPath(resolvedPath);
      const response = await api.createProject(resolvedPath, targetNodeId);
      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          (payload && typeof payload.detail === 'string' && payload.detail) ||
            (payload && typeof payload.error === 'string' && payload.error) ||
            t('workspaceSessions.messages.createFailed'),
        );
      }

      const createdProject = normalizeCreatedProject(
        payload?.project as Partial<Project> | undefined,
        resolvedPath,
        targetNodeId,
        targetNode?.displayName || null,
      );

      handleCreateDraft(createdProject, newSessionProvider);
      void onRefresh();
    } catch (error) {
      setNewSessionError(
        error instanceof Error ? error.message : t('workspaceSessions.messages.createError'),
      );
    } finally {
      setIsCreatingSession(false);
    }
  };

  if (isSidebarCollapsed) {
    return (
      <>
        <SidebarModals
          projects={projects}
          showSettings={showSettings}
          settingsInitialTab={settingsInitialTab}
          onCloseSettings={onCloseSettings}
          deleteConfirmation={null}
          onCancelDeleteProject={() => undefined}
          onConfirmDeleteProject={() => undefined}
          sessionDeleteConfirmation={null}
          onCancelDeleteSession={() => undefined}
          onConfirmDeleteSession={() => undefined}
          showVersionModal={showVersionModal}
          onCloseVersionModal={() => setShowVersionModal(false)}
          releaseInfo={releaseInfo}
          currentVersion={currentVersion}
          latestVersion={latestVersion}
          installMode={installMode}
          t={t}
        />

        <SidebarCollapsed
          onExpand={expandSidebar}
          onShowSettings={onShowSettings}
          updateAvailable={updateAvailable}
          onShowVersionModal={() => setShowVersionModal(true)}
          t={t}
        />
      </>
    );
  }

  return (
    <>
      <SidebarModals
        projects={projects}
        showSettings={showSettings}
        settingsInitialTab={settingsInitialTab}
        onCloseSettings={onCloseSettings}
        deleteConfirmation={null}
        onCancelDeleteProject={() => undefined}
        onConfirmDeleteProject={() => undefined}
        sessionDeleteConfirmation={null}
        onCancelDeleteSession={() => undefined}
        onConfirmDeleteSession={() => undefined}
        showVersionModal={showVersionModal}
        onCloseVersionModal={() => setShowVersionModal(false)}
        releaseInfo={releaseInfo}
        currentVersion={currentVersion}
        latestVersion={latestVersion}
        installMode={installMode}
        t={t}
      />

      <div className="flex h-full flex-col bg-background/80 backdrop-blur-sm md:w-80 md:select-none">
        <div className="flex-shrink-0">
          <div className="hidden px-3 pb-3 pt-3 md:block">
            <SidebarHeader
              title={t('app.title')}
              nodes={nodes}
              isRefreshing={isRefreshing}
              onRefresh={() => void handleRefresh()}
              onCollapse={collapseSidebar}
              onRemoveNode={removeNode}
            />
          </div>

          <div className="p-3 pb-3 md:hidden" style={isPWA && isMobile ? { paddingTop: '16px' } : {}}>
            <SidebarHeader
              title={t('app.title')}
              nodes={nodes}
              isRefreshing={isRefreshing}
              onRefresh={() => void handleRefresh()}
              onCollapse={collapseSidebar}
              onRemoveNode={removeNode}
            />
          </div>

          <div className="px-3 pb-3">
            <div className="grid grid-cols-2 gap-2">
              <ActionCardButton
                label={t('workspaceSessions.newActionLabel')}
                onClick={() => openPicker('new')}
                icon={<MessageSquarePlus className="h-4 w-4" />}
              />
              <ActionCardButton
                label={t('workspaceSessions.openActionLabel')}
                onClick={() => openPicker('existing')}
                icon={<Search className="h-4 w-4" />}
              />
            </div>

            <div className="relative mt-3">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/50" />
              <Input
                type="text"
                value={openSessionsSearch}
                onChange={(event) => setOpenSessionsSearch(event.target.value)}
                placeholder={t('workspaceSessions.searchPinnedPlaceholder')}
                className="h-9 rounded-xl border-0 pl-9 pr-8 text-sm focus-visible:ring-0 focus-visible:ring-offset-0"
              />
              {openSessionsSearch && (
                <button
                  type="button"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 hover:bg-accent"
                  onClick={() => setOpenSessionsSearch('')}
                  aria-label={t('tooltips.clearSearch')}
                >
                  <X className="h-3.5 w-3.5 text-muted-foreground" />
                </button>
              )}
            </div>
          </div>

          <div className="nav-divider" />
        </div>

        <ScrollArea className="flex-1 overflow-y-auto overscroll-contain pb-3">
          {pickerMode === 'new' && (
            <NewSessionPanel
              nodes={nodes}
              nodeId={newSessionNodeId}
              provider={newSessionProvider}
              path={newSessionPath}
              pathStatus={newSessionPathStatus}
              pathSuggestions={newSessionPathSuggestions}
              isCheckingPath={isCheckingNewSessionPath}
              isSubmitting={isCreatingSession}
              error={newSessionError}
              onNodeChange={handleNewSessionNodeChange}
              onProviderChange={setNewSessionProvider}
              onPathChange={handleNewSessionPathChange}
              onSelectSuggestion={handleSelectNewSessionSuggestion}
              onSubmit={() => void handleCreateSession()}
              onClose={closePicker}
            />
          )}

          {pickerMode === 'existing' && (
            <OpenSessionPickerPanel
              searchValue={pickerSearch}
              onSearchChange={setPickerSearch}
              onClose={closePicker}
              sessions={filteredAvailableSessions}
              onOpenSession={handleOpenExistingSession}
            />
          )}

          <div className="flex items-start justify-between gap-3 px-3 pb-2 pt-3">
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                {t('workspaceSessions.pinnedSectionTitle')}
              </h2>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {isLoading
                  ? t('workspaceSessions.pinnedCountLoading')
                  : t('workspaceSessions.pinnedCount', { count: openSessions.length })}
              </p>
            </div>
            <SessionViewModeToggle
              value={sessionListViewMode}
              onChange={setSessionListViewMode}
            />
          </div>

          <div className="space-y-2 px-3 pb-safe-area-inset-bottom">
            {isLoading && openSessions.length === 0 ? (
              <div className="rounded-2xl border border-border/60 bg-card/60 px-4 py-8 text-center text-sm text-muted-foreground">
                {t('workspaceSessions.loadingPinned')}
              </div>
            ) : openSessions.length === 0 ? (
              <EmptyOpenSessionsState />
            ) : filteredOpenSessions.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border/70 bg-card/40 px-4 py-6 text-center text-sm text-muted-foreground">
                {t('workspaceSessions.emptySearch')}
              </div>
            ) : sessionListViewMode === 'grouped' ? (
              <SessionTreeView
                entries={filteredOpenSessions}
                activeSessionKey={activeSessionKey}
                searchValue={openSessionsSearch}
                onOpenSession={onOpenSession}
                onCloseSession={onCloseSession}
                emptyLabel={t('workspaceSessions.emptySearch')}
              />
            ) : (
              filteredOpenSessions.map((entry) => (
                <OpenSessionItem
                  key={entry.key}
                  entry={entry}
                  isActive={activeSessionKey === entry.key}
                  onOpen={onOpenSession}
                  onClose={onCloseSession}
                />
              ))
            )}
          </div>
        </ScrollArea>

        <SidebarFooter
          updateAvailable={updateAvailable}
          releaseInfo={releaseInfo}
          latestVersion={latestVersion}
          onShowVersionModal={() => setShowVersionModal(true)}
          onShowSettings={onShowSettings}
          t={t}
        />
      </div>
    </>
  );
}
