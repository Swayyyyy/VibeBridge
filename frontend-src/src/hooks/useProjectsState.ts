import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { NavigateFunction } from 'react-router-dom';
import { useNodes } from '../contexts/NodeContext';
import { api } from '../utils/api';
import type {
  AppSocketMessage,
  AppTab,
  LoadingProgress,
  MainAreaTab,
  OpenSessionEntry,
  Project,
  ProjectSession,
  ProjectsUpdatedMessage,
  SessionProvider,
  UtilityPanelTab,
} from '../types/app';

type UseProjectsStateArgs = {
  routeSessionId?: string;
  routeSessionKey?: string;
  routeNodeId?: string;
  routeProjectName?: string;
  routeProvider?: SessionProvider;
  routeDraft?: boolean;
  navigate: NavigateFunction;
  latestMessage: AppSocketMessage | null;
  isMobile: boolean;
  activeSessions: Set<string>;
};

type FetchProjectsOptions = {
  showLoadingState?: boolean;
};

const NODE_LIST_TIMEOUT_MS = 5000;
const NODE_PROJECTS_TIMEOUT_MS = 8000;

type RemoteNodeSummary = {
  nodeId: string;
  displayName?: string;
  status?: string;
};

type SessionMatch = {
  project: Project;
  session: ProjectSession;
  provider: SessionProvider;
};

const VALID_TABS: Set<string> = new Set(['chat', 'files', 'shell', 'git', 'tasks', 'preview']);
const UTILITY_PANEL_TABS: Set<UtilityPanelTab> = new Set(['files', 'shell', 'git']);
const MAIN_TAB_STORAGE_KEY = 'mainTab-v1';
const UTILITY_PANEL_TAB_STORAGE_KEY = 'utilityPanelTab-v1';
const OPEN_SESSIONS_STORAGE_KEY = 'open-sessions-v2';
const ACTIVE_SESSION_STORAGE_KEY = 'active-open-session-v2';
const DRAFT_PREFIX = 'draft-';

const serialize = (value: unknown) => JSON.stringify(value ?? null);

const isValidTab = (tab: string): tab is AppTab => {
  return VALID_TABS.has(tab) || tab.startsWith('plugin:');
};

const isUtilityPanelTab = (tab: string): tab is UtilityPanelTab => {
  return UTILITY_PANEL_TABS.has(tab as UtilityPanelTab);
};

const isMainAreaTab = (tab: string): tab is MainAreaTab => {
  return isValidTab(tab) && !isUtilityPanelTab(tab);
};

const readPersistedActiveTab = (): AppTab => {
  try {
    const stored = localStorage.getItem('activeTab');
    if (stored && isValidTab(stored)) {
      return stored as AppTab;
    }
  } catch {
    // localStorage unavailable
  }
  return 'chat';
};

const readPersistedMainTab = (): MainAreaTab => {
  try {
    const stored = localStorage.getItem(MAIN_TAB_STORAGE_KEY);
    if (stored && isMainAreaTab(stored)) {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }

  const activeTab = readPersistedActiveTab();
  return isMainAreaTab(activeTab) ? activeTab : 'chat';
};

const readPersistedUtilityPanelTab = (): UtilityPanelTab | null => {
  try {
    const stored = localStorage.getItem(UTILITY_PANEL_TAB_STORAGE_KEY);
    if (stored && isUtilityPanelTab(stored)) {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }

  const activeTab = readPersistedActiveTab();
  return isUtilityPanelTab(activeTab) ? activeTab : null;
};

const readPersistedOpenSessions = (): OpenSessionEntry[] => {
  try {
    const stored = localStorage.getItem(OPEN_SESSIONS_STORAGE_KEY);
    if (!stored) {
      return [];
    }

    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) {
      return [];
    }

    return parsed
      .filter((entry): entry is OpenSessionEntry => {
        if (!entry || typeof entry !== 'object') {
          return false;
        }

        return (
          typeof entry.key === 'string' &&
          typeof entry.title === 'string' &&
          typeof entry.projectName === 'string' &&
          typeof entry.projectDisplayName === 'string' &&
          typeof entry.projectPath === 'string' &&
          typeof entry.lastOpenedAt === 'string'
        );
      })
      .map((entry) => ({
        ...entry,
        sessionId: typeof entry.sessionId === 'string' ? entry.sessionId : null,
        provider:
          entry.provider === 'claude' || entry.provider === 'codex'
            ? entry.provider
            : undefined,
        nodeId: typeof entry.nodeId === 'string' ? entry.nodeId : null,
        nodeDisplayName:
          typeof entry.nodeDisplayName === 'string' ? entry.nodeDisplayName : null,
        isDraft: Boolean(entry.isDraft),
      }));
  } catch {
    return [];
  }
};

const readPersistedActiveSessionKey = (): string | null => {
  try {
    const stored = localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
    return stored && stored.length > 0 ? stored : null;
  } catch {
    return null;
  }
};

const getProjectPath = (project: Pick<Project, 'fullPath' | 'path'>): string => {
  return project.fullPath || project.path || '';
};

const sanitizeDraftSegment = (value: string | null | undefined): string => {
  return (value || 'local').replace(/[^a-zA-Z0-9_-]/g, '-');
};

const withTimeout = <T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> => {
  return new Promise<T>((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      reject(new Error(`${label} timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    promise.then(
      (value) => {
        window.clearTimeout(timeoutId);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timeoutId);
        reject(error);
      },
    );
  });
};

const makeDraftKey = (
  project: Project,
  provider: SessionProvider = 'claude',
): string => {
  const nodeSegment = sanitizeDraftSegment(project.nodeId);
  const projectSegment = sanitizeDraftSegment(project.name);
  const providerSegment = sanitizeDraftSegment(provider);
  return `${DRAFT_PREFIX}${nodeSegment}-${projectSegment}-${providerSegment}`;
};

const getSessionTitle = (
  session: Pick<ProjectSession, 'summary' | 'name' | 'title' | 'id'>,
  provider?: SessionProvider,
): string => {
  const preferred =
    (typeof session.summary === 'string' && session.summary.trim()) ||
    (typeof session.name === 'string' && session.name.trim()) ||
    (typeof session.title === 'string' && session.title.trim());

  if (preferred) {
    return preferred;
  }

  if (provider === 'codex') {
    return 'Codex Session';
  }

  return 'New Session';
};

const getSessionTimestamp = (session: ProjectSession): string | undefined => {
  return (
    (typeof session.updated_at === 'string' && session.updated_at) ||
    (typeof session.lastActivity === 'string' && session.lastActivity) ||
    (typeof session.createdAt === 'string' && session.createdAt) ||
    (typeof session.created_at === 'string' && session.created_at) ||
    undefined
  );
};

const normalizeProject = (
  project: Project,
  nodeId: string | null = null,
  nodeDisplayName: string | null = null,
): Project => {
  const normalizedPath = getProjectPath(project);
  return {
    ...project,
    displayName: project.displayName || project.name,
    fullPath: normalizedPath,
    path: project.path || normalizedPath,
    nodeId,
    nodeDisplayName,
  };
};

const buildSessionWithMetadata = (
  project: Project,
  session: ProjectSession,
  provider: SessionProvider,
): ProjectSession => {
  return {
    ...session,
    __provider: provider,
    __projectName: project.name,
    __projectDisplayName: project.displayName,
    __projectPath: getProjectPath(project),
    __nodeId: project.nodeId ?? null,
    __nodeDisplayName: project.nodeDisplayName ?? null,
    __openSessionKey: session.id,
  };
};

const buildOpenSessionEntryFromMatch = ({
  project,
  session,
  provider,
}: SessionMatch): OpenSessionEntry => {
  return {
    key: session.id,
    sessionId: session.id,
    title: getSessionTitle(session, provider),
    provider,
    projectName: project.name,
    projectDisplayName: project.displayName,
    projectPath: getProjectPath(project),
    nodeId: project.nodeId ?? null,
    nodeDisplayName: project.nodeDisplayName ?? null,
    createdAt:
      (typeof session.createdAt === 'string' && session.createdAt) ||
      (typeof session.created_at === 'string' && session.created_at) ||
      undefined,
    updatedAt: getSessionTimestamp(session),
    isDraft: false,
    lastOpenedAt: new Date().toISOString(),
  };
};

const buildDraftEntry = (
  project: Project,
  provider: SessionProvider = 'claude',
): OpenSessionEntry => {
  return {
    key: makeDraftKey(project, provider),
    sessionId: null,
    title: 'New Session',
    provider,
    projectName: project.name,
    projectDisplayName: project.displayName,
    projectPath: getProjectPath(project),
    nodeId: project.nodeId ?? null,
    nodeDisplayName: project.nodeDisplayName ?? null,
    isDraft: true,
    lastOpenedAt: new Date().toISOString(),
  };
};

const buildSessionRoute = (entry: OpenSessionEntry): string => {
  const searchParams = new URLSearchParams();

  if (entry.nodeId) {
    searchParams.set('node', entry.nodeId);
  }

  if (entry.projectName) {
    searchParams.set('project', entry.projectName);
  }

  if (entry.provider) {
    searchParams.set('provider', entry.provider);
  }

  if (entry.isDraft || !entry.sessionId) {
    searchParams.set('draft', '1');
  } else {
    searchParams.set('session', entry.sessionId);
  }

  return `/session?${searchParams.toString()}`;
};

const findProjectMatch = (
  projects: Project[],
  projectName?: string,
  nodeId?: string | null,
): Project | null => {
  if (!projectName) {
    return null;
  }

  return (
    projects.find(
      (project) =>
        project.name === projectName &&
        (nodeId === undefined || compareNullableString(project.nodeId, nodeId)),
    ) || null
  );
};

const findCachedRouteEntry = (
  entries: OpenSessionEntry[],
  routeSessionKey?: string,
  routeSessionId?: string,
  routeDraft?: boolean,
  routeNodeId?: string | null,
  routeProjectName?: string,
  routeProvider?: SessionProvider,
): OpenSessionEntry | null => {
  if (routeSessionKey) {
    const exactKeyMatch = entries.find((entry) => entry.key === routeSessionKey) || null;
    if (exactKeyMatch) {
      return exactKeyMatch;
    }
  }

  if (routeDraft) {
    return (
      entries.find(
        (entry) =>
          Boolean(entry.isDraft) &&
          entry.projectName === routeProjectName &&
          compareNullableString(entry.nodeId, routeNodeId ?? null) &&
          (!routeProvider || entry.provider === routeProvider),
      ) || null
    );
  }

  if (!routeSessionId) {
    return null;
  }

  return (
    entries.find(
      (entry) =>
        entry.sessionId === routeSessionId &&
        (!routeProjectName || entry.projectName === routeProjectName) &&
        (routeNodeId === undefined || compareNullableString(entry.nodeId, routeNodeId ?? null)),
    ) ||
    entries.find((entry) => entry.sessionId === routeSessionId) ||
    null
  );
};

const buildFallbackProject = (entry: OpenSessionEntry): Project => {
  return {
    name: entry.projectName,
    displayName: entry.projectDisplayName,
    fullPath: entry.projectPath,
    path: entry.projectPath,
    nodeId: entry.nodeId ?? null,
    nodeDisplayName: entry.nodeDisplayName ?? null,
    sessions: [],
    codexSessions: [],
    sessionMeta: { hasMore: false, total: 0 },
  };
};

const buildFallbackSession = (entry: OpenSessionEntry): ProjectSession | null => {
  if (!entry.sessionId) {
    return null;
  }

  return {
    id: entry.sessionId,
    summary: entry.title,
    name: entry.title,
    title: entry.title,
    updated_at: entry.updatedAt,
    created_at: entry.createdAt,
    __provider: entry.provider,
    __projectName: entry.projectName,
    __projectDisplayName: entry.projectDisplayName,
    __projectPath: entry.projectPath,
    __nodeId: entry.nodeId ?? null,
    __nodeDisplayName: entry.nodeDisplayName ?? null,
    __openSessionKey: entry.key,
    __isDraft: false,
  };
};

const compareNullableString = (left: string | null | undefined, right: string | null | undefined) => {
  return (left ?? null) === (right ?? null);
};

const findSessionMatch = (
  projects: Project[],
  sessionId: string,
  hints?: { projectName?: string; nodeId?: string | null },
): SessionMatch | null => {
  const tryMatch = (restrictToHints: boolean): SessionMatch | null => {
    for (const project of projects) {
      if (restrictToHints) {
        if (hints?.projectName && project.name !== hints.projectName) {
          continue;
        }
        if (
          hints &&
          'nodeId' in hints &&
          !compareNullableString(project.nodeId, hints.nodeId)
        ) {
          continue;
        }
      }

      const claudeSession = project.sessions?.find((session) => session.id === sessionId);
      if (claudeSession) {
        return { project, session: claudeSession, provider: 'claude' };
      }

      const codexSession = project.codexSessions?.find((session) => session.id === sessionId);
      if (codexSession) {
        return { project, session: codexSession, provider: 'codex' };
      }
    }

    return null;
  };

  return tryMatch(true) || tryMatch(false);
};

const upsertOpenSessionEntries = (
  previous: OpenSessionEntry[],
  entry: OpenSessionEntry,
): OpenSessionEntry[] => {
  const touchedEntry = { ...entry, lastOpenedAt: new Date().toISOString() };

  const nextEntries = previous.filter((candidate) => {
    if (candidate.key === touchedEntry.key) {
      return false;
    }

    if (touchedEntry.sessionId && candidate.sessionId === touchedEntry.sessionId) {
      return false;
    }

    return true;
  });

  return [touchedEntry, ...nextEntries];
};

const syncOpenSessionsWithProjects = (
  entries: OpenSessionEntry[],
  projects: Project[],
): OpenSessionEntry[] => {
  let hasChanges = false;

  const syncedEntries = entries.map((entry) => {
    if (entry.isDraft || !entry.sessionId) {
      return entry;
    }

    const match = findSessionMatch(projects, entry.sessionId, {
      projectName: entry.projectName,
      nodeId: entry.nodeId ?? null,
    });

    if (!match) {
      return entry;
    }

    const refreshed = {
      ...entry,
      ...buildOpenSessionEntryFromMatch(match),
      key: entry.key,
      sessionId: entry.sessionId,
      isDraft: false,
      lastOpenedAt: entry.lastOpenedAt,
    };

    if (serialize(refreshed) !== serialize(entry)) {
      hasChanges = true;
    }

    return refreshed;
  });

  return hasChanges ? syncedEntries : entries;
};

const projectsHaveChanges = (previous: Project[], next: Project[]): boolean => {
  if (previous.length !== next.length) {
    return true;
  }

  return next.some((nextProject, index) => {
    const prevProject = previous[index];
    if (!prevProject) {
      return true;
    }

    return (
      nextProject.name !== prevProject.name ||
      nextProject.displayName !== prevProject.displayName ||
      nextProject.fullPath !== prevProject.fullPath ||
      !compareNullableString(nextProject.nodeId, prevProject.nodeId) ||
      !compareNullableString(nextProject.nodeDisplayName, prevProject.nodeDisplayName) ||
      serialize(nextProject.sessionMeta) !== serialize(prevProject.sessionMeta) ||
      serialize(nextProject.sessions) !== serialize(prevProject.sessions) ||
      serialize(nextProject.codexSessions) !== serialize(prevProject.codexSessions) ||
      serialize(nextProject.taskmaster) !== serialize(prevProject.taskmaster)
    );
  });
};

const sortProjects = (projects: Project[]): Project[] => {
  return [...projects].sort((left, right) => {
    const nodeCompare = (left.nodeDisplayName || '').localeCompare(right.nodeDisplayName || '');
    if (nodeCompare !== 0) {
      return nodeCompare;
    }

    const nameCompare = (left.displayName || left.name).localeCompare(right.displayName || right.name);
    if (nameCompare !== 0) {
      return nameCompare;
    }

    return getProjectPath(left).localeCompare(getProjectPath(right));
  });
};

export function useProjectsState({
  routeSessionId,
  routeSessionKey,
  routeNodeId,
  routeProjectName,
  routeProvider,
  routeDraft,
  navigate,
  latestMessage,
  isMobile,
  activeSessions,
}: UseProjectsStateArgs) {
  const { selectNode } = useNodes();

  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [selectedSession, setSelectedSession] = useState<ProjectSession | null>(null);
  const [openSessions, setOpenSessions] = useState<OpenSessionEntry[]>(readPersistedOpenSessions);
  const [activeSessionKey, setActiveSessionKey] = useState<string | null>(readPersistedActiveSessionKey);
  const [mainTab, setMainTab] = useState<MainAreaTab>(readPersistedMainTab);
  const [utilityPanelTab, setUtilityPanelTab] = useState<UtilityPanelTab | null>(
    readPersistedUtilityPanelTab,
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isLoadingProjects, setIsLoadingProjects] = useState(true);
  const [loadingProgress, setLoadingProgress] = useState<LoadingProgress | null>(null);
  const [isInputFocused, setIsInputFocused] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsInitialTab, setSettingsInitialTab] = useState('agents');
  const [externalMessageUpdate, setExternalMessageUpdate] = useState(0);

  const loadingProgressTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasRestoredInitialRouteRef = useRef(false);
  const activeTab = utilityPanelTab ?? mainTab;

  const setActiveTab = useCallback(
    (value: AppTab | ((previous: AppTab) => AppTab)) => {
      const nextTab = typeof value === 'function' ? value(activeTab) : value;

      if (isUtilityPanelTab(nextTab)) {
        setUtilityPanelTab((previous) => (previous === nextTab ? null : nextTab));
        return;
      }

      setMainTab(nextTab);
      setUtilityPanelTab(null);
    },
    [activeTab],
  );

  const closeUtilityPanel = useCallback(() => {
    setUtilityPanelTab(null);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem('activeTab', activeTab);
      localStorage.setItem(MAIN_TAB_STORAGE_KEY, mainTab);
      if (utilityPanelTab) {
        localStorage.setItem(UTILITY_PANEL_TAB_STORAGE_KEY, utilityPanelTab);
      } else {
        localStorage.removeItem(UTILITY_PANEL_TAB_STORAGE_KEY);
      }
    } catch {
      // localStorage unavailable
    }
  }, [activeTab, mainTab, utilityPanelTab]);

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_SESSIONS_STORAGE_KEY, JSON.stringify(openSessions));
    } catch {
      // localStorage unavailable
    }
  }, [openSessions]);

  useEffect(() => {
    try {
      if (activeSessionKey) {
        localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, activeSessionKey);
      } else {
        localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
      }
    } catch {
      // localStorage unavailable
    }
  }, [activeSessionKey]);

  const fetchProjects = useCallback(
    async ({ showLoadingState = true }: FetchProjectsOptions = {}) => {
      try {
        if (showLoadingState) {
          setIsLoadingProjects(true);
        }

        let projectData: Project[] = [];

        try {
          const nodesResponse = await withTimeout(
            api.nodes.list(),
            NODE_LIST_TIMEOUT_MS,
            'Fetching nodes',
          );
          if (nodesResponse.ok) {
            const nodePayload = await nodesResponse.json();
            const nodes: RemoteNodeSummary[] = Array.isArray(nodePayload?.nodes) ? nodePayload.nodes : [];
            const onlineNodes = nodes.filter(
              (node): node is RemoteNodeSummary =>
                Boolean(node?.nodeId) && node?.status === 'online',
            );

            if (onlineNodes.length > 0) {
              const fetchedProjects = await Promise.allSettled(
                onlineNodes.map(async (node) => {
                  try {
                    const response = await withTimeout(
                      api.nodes.projects(node.nodeId),
                      NODE_PROJECTS_TIMEOUT_MS,
                      `Fetching projects for node ${node.nodeId}`,
                    );
                    if (!response.ok) {
                      return [];
                    }

                    const nodeProjects = (await response.json()) as Project[];
                    return nodeProjects.map((project) =>
                      normalizeProject(project, node.nodeId, node.displayName || node.nodeId),
                    );
                  } catch (error) {
                    console.error(`Error fetching projects for node ${node.nodeId}:`, error);
                    return [];
                  }
                }),
              );

              projectData = fetchedProjects.flatMap((result) =>
                result.status === 'fulfilled' ? result.value : [],
              );
            } else if (nodes.length > 0) {
              projectData = [];
            } else {
              projectData = [];
            }
          } else {
            const response = await api.projects();
            const localProjects = (await response.json()) as Project[];
            projectData = localProjects.map((project) => normalizeProject(project));
          }
        } catch {
          const response = await api.projects();
          const localProjects = (await response.json()) as Project[];
          projectData = localProjects.map((project) => normalizeProject(project));
        }

        const sortedProjects = sortProjects(projectData);

        setProjects((previous) => {
          return projectsHaveChanges(previous, sortedProjects) ? sortedProjects : previous;
        });
      } catch (error) {
        console.error('Error fetching projects:', error);
      } finally {
        if (showLoadingState) {
          setIsLoadingProjects(false);
        }
      }
    },
    [],
  );

  const refreshProjectsSilently = useCallback(async () => {
    await fetchProjects({ showLoadingState: false });
  }, [fetchProjects]);

  const openSettings = useCallback((tab = 'tools') => {
    setSettingsInitialTab(tab);
    setShowSettings(true);
  }, []);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  useEffect(() => {
    setOpenSessions((previous) => syncOpenSessionsWithProjects(previous, projects));
  }, [projects]);

  const availableSessions = useMemo(() => {
    const flattenedSessions: OpenSessionEntry[] = [];

    projects.forEach((project) => {
      project.sessions?.forEach((session) => {
        flattenedSessions.push(
          buildOpenSessionEntryFromMatch({
            project,
            session,
            provider: 'claude',
          }),
        );
      });

      project.codexSessions?.forEach((session) => {
        flattenedSessions.push(
          buildOpenSessionEntryFromMatch({
            project,
            session,
            provider: 'codex',
          }),
        );
      });
    });

    return flattenedSessions.sort((left, right) => {
      const rightTime = right.updatedAt || right.createdAt || '';
      const leftTime = left.updatedAt || left.createdAt || '';
      return rightTime.localeCompare(leftTime);
    });
  }, [projects]);

  const syncViewFromEntry = useCallback(
    (entry: OpenSessionEntry | null) => {
      if (!entry) {
        setSelectedProject(null);
        setSelectedSession(null);
        return;
      }

      const match =
        entry.sessionId && !entry.isDraft
          ? findSessionMatch(projects, entry.sessionId, {
              projectName: entry.projectName,
              nodeId: entry.nodeId ?? null,
            })
          : null;

      const matchedNodeId = match?.project.nodeId ?? entry.nodeId;
      if (matchedNodeId) {
        selectNode(matchedNodeId);
      }

      if (entry.isDraft && entry.provider) {
        try {
          localStorage.setItem('selected-provider', entry.provider);
        } catch {
          // localStorage unavailable
        }
      }

      const nextProject = match?.project || buildFallbackProject(entry);
      const nextSession =
        entry.isDraft || !entry.sessionId
          ? null
          : match
            ? buildSessionWithMetadata(match.project, match.session, match.provider)
            : buildFallbackSession(entry);

      setSelectedProject((previous) => {
        return serialize(previous) === serialize(nextProject) ? previous : nextProject;
      });
      setSelectedSession((previous) => {
        return serialize(previous) === serialize(nextSession) ? previous : nextSession;
      });
    },
    [projects, selectNode],
  );

  const activateOpenSession = useCallback(
    (entry: OpenSessionEntry) => {
      const nextEntries = upsertOpenSessionEntries(openSessions, entry);
      const nextEntry = nextEntries[0];

      setOpenSessions(nextEntries);
      setActiveSessionKey(nextEntry.key);
      syncViewFromEntry(nextEntry);
      navigate(buildSessionRoute(nextEntry));

      if (isMobile) {
        setSidebarOpen(false);
      }
    },
    [isMobile, navigate, openSessions, syncViewFromEntry],
  );

  const closeOpenSession = useCallback(
    (sessionKey: string) => {
      const nextEntries = openSessions.filter((entry) => entry.key !== sessionKey);
      const wasActive = activeSessionKey === sessionKey;

      setOpenSessions(nextEntries);

      if (!wasActive) {
        return;
      }

      const nextActiveEntry = nextEntries[0] || null;

      if (nextActiveEntry) {
        setActiveSessionKey(nextActiveEntry.key);
        syncViewFromEntry(nextActiveEntry);
        navigate(buildSessionRoute(nextActiveEntry));
      } else {
        setActiveSessionKey(null);
        syncViewFromEntry(null);
        navigate('/');
      }
    },
    [activeSessionKey, navigate, openSessions, syncViewFromEntry],
  );

  const createDraftSession = useCallback(
    (project: Project, provider: SessionProvider = 'claude') => {
      const existingDraft = openSessions.find(
        (entry) =>
          entry.isDraft &&
          entry.projectName === project.name &&
          compareNullableString(entry.nodeId, project.nodeId ?? null) &&
          entry.provider === provider,
      );

      if (existingDraft) {
        activateOpenSession(existingDraft);
        return;
      }

      activateOpenSession(buildDraftEntry(project, provider));
    },
    [activateOpenSession, openSessions],
  );

  const handleSidebarRefresh = useCallback(async () => {
    await fetchProjects();
  }, [fetchProjects]);

  const handleNavigateToSession = useCallback(
    (targetSessionId: string) => {
      const activeEntry = activeSessionKey
        ? openSessions.find((entry) => entry.key === activeSessionKey) || null
        : null;
      const match = findSessionMatch(projects, targetSessionId);

      let nextEntry: OpenSessionEntry;

      if (match) {
        nextEntry = buildOpenSessionEntryFromMatch(match);
      } else if (activeEntry) {
        const providerFromStorage = localStorage.getItem('selected-provider');
        const inferredProvider: SessionProvider =
          providerFromStorage === 'codex' ? 'codex' : 'claude';

        nextEntry = {
          ...activeEntry,
          key: targetSessionId,
          sessionId: targetSessionId,
          provider: activeEntry.provider || inferredProvider,
          isDraft: false,
          title:
            activeEntry.title && activeEntry.title.trim().length > 0
              ? activeEntry.title
              : getSessionTitle({ id: targetSessionId }, activeEntry.provider || inferredProvider),
          lastOpenedAt: new Date().toISOString(),
        };
      } else {
        const searchParams = new URLSearchParams();
        searchParams.set('session', targetSessionId);
        navigate(`/session?${searchParams.toString()}`);
        return;
      }

      const baseEntries =
        activeEntry?.isDraft && activeEntry.key
          ? openSessions.filter((entry) => entry.key !== activeEntry.key)
          : openSessions;

      setOpenSessions(upsertOpenSessionEntries(baseEntries, nextEntry));
      setActiveSessionKey(nextEntry.key);
      syncViewFromEntry(nextEntry);
      navigate(buildSessionRoute(nextEntry));
    },
    [activeSessionKey, navigate, openSessions, projects, syncViewFromEntry],
  );

  useEffect(() => {
    if (hasRestoredInitialRouteRef.current) {
      return;
    }

    const hasRouteSelection = Boolean(routeSessionKey || routeSessionId || routeDraft);
    if (hasRouteSelection) {
      hasRestoredInitialRouteRef.current = true;
      return;
    }

    if (openSessions.length === 0) {
      hasRestoredInitialRouteRef.current = true;
      return;
    }

    const preferredEntry =
      openSessions.find((entry) => entry.key === activeSessionKey) || openSessions[0] || null;
    if (!preferredEntry?.key) {
      hasRestoredInitialRouteRef.current = true;
      return;
    }

    hasRestoredInitialRouteRef.current = true;
    navigate(buildSessionRoute(preferredEntry), { replace: true });
  }, [activeSessionKey, navigate, openSessions, routeDraft, routeSessionId, routeSessionKey]);

  useEffect(() => {
    const hasRouteSelection = Boolean(routeSessionKey || routeSessionId || routeDraft);
    if (!hasRouteSelection) {
      if (hasRestoredInitialRouteRef.current) {
        setActiveSessionKey(null);
        syncViewFromEntry(null);
      }
      return;
    }

    const cachedEntry = findCachedRouteEntry(
      openSessions,
      routeSessionKey,
      routeSessionId,
      routeDraft,
      routeNodeId,
      routeProjectName,
      routeProvider,
    );

    if (cachedEntry) {
      if (activeSessionKey !== cachedEntry.key) {
        setActiveSessionKey(cachedEntry.key);
      }
      syncViewFromEntry(cachedEntry);
      return;
    }

    if (routeDraft) {
      const matchedProject = findProjectMatch(projects, routeProjectName, routeNodeId);
      if (matchedProject) {
        const entry = buildDraftEntry(matchedProject, routeProvider || 'claude');
        setOpenSessions((previous) => upsertOpenSessionEntries(previous, entry));
        setActiveSessionKey(entry.key);
        syncViewFromEntry(entry);
        return;
      }

      if (isLoadingProjects) {
        return;
      }

      setActiveSessionKey(null);
      syncViewFromEntry(null);
      return;
    }

    if (!routeSessionId) {
      if (isLoadingProjects) {
        return;
      }

      setActiveSessionKey(null);
      syncViewFromEntry(null);
      return;
    }

    const routeHints =
      routeProjectName || routeNodeId !== undefined
        ? {
            projectName: routeProjectName,
            ...(routeNodeId !== undefined ? { nodeId: routeNodeId } : {}),
          }
        : undefined;
    const match = findSessionMatch(projects, routeSessionId, routeHints);
    if (match) {
      const entry = buildOpenSessionEntryFromMatch(match);
      setOpenSessions((previous) => upsertOpenSessionEntries(previous, entry));
      setActiveSessionKey(entry.key);
      syncViewFromEntry(entry);
      return;
    }

    if (isLoadingProjects) {
      return;
    }

    setActiveSessionKey(null);
    syncViewFromEntry(null);
  }, [
    activeSessionKey,
    isLoadingProjects,
    openSessions,
    projects,
    routeDraft,
    routeNodeId,
    routeProjectName,
    routeProvider,
    routeSessionId,
    routeSessionKey,
    syncViewFromEntry,
  ]);

  useEffect(() => {
    if (!latestMessage) {
      return;
    }

    if (latestMessage.type === 'loading_progress') {
      if (loadingProgressTimeoutRef.current) {
        clearTimeout(loadingProgressTimeoutRef.current);
        loadingProgressTimeoutRef.current = null;
      }

      setLoadingProgress(latestMessage as LoadingProgress);

      if (latestMessage.phase === 'complete') {
        loadingProgressTimeoutRef.current = setTimeout(() => {
          setLoadingProgress(null);
          loadingProgressTimeoutRef.current = null;
        }, 500);
      }

      return;
    }

    if (latestMessage.type !== 'projects_updated') {
      return;
    }

    const projectsMessage = latestMessage as ProjectsUpdatedMessage;

    if (projectsMessage.changedFile && selectedSession) {
      const normalized = projectsMessage.changedFile.replace(/\\/g, '/');
      const changedFileParts = normalized.split('/');

      if (changedFileParts.length >= 2) {
        const filename = changedFileParts[changedFileParts.length - 1];
        const changedSessionId = filename.replace('.jsonl', '');

        if (changedSessionId === selectedSession.id && !activeSessions.has(selectedSession.id)) {
          setExternalMessageUpdate((previous) => previous + 1);
        }
      }
    }

    void fetchProjects({ showLoadingState: false });
  }, [activeSessions, fetchProjects, latestMessage, selectedSession]);

  useEffect(() => {
    return () => {
      if (loadingProgressTimeoutRef.current) {
        clearTimeout(loadingProgressTimeoutRef.current);
        loadingProgressTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const activeEntry = activeSessionKey
      ? openSessions.find((entry) => entry.key === activeSessionKey) || null
      : null;

    let baseTitle = 'VibeBridge';

    if (activeEntry) {
      const location =
        activeEntry.nodeDisplayName && activeEntry.nodeDisplayName.trim().length > 0
          ? `${activeEntry.nodeDisplayName} · ${activeEntry.projectDisplayName}`
          : activeEntry.projectDisplayName;
      baseTitle = `${activeEntry.title} - ${location} - ${baseTitle}`;
    } else if (selectedProject?.displayName) {
      baseTitle = `${selectedProject.displayName} - ${baseTitle}`;
    }

    document.title = baseTitle;
  }, [activeSessionKey, openSessions, selectedProject?.displayName]);

  const selectedOpenSessionEntry = useMemo(() => {
    if (activeSessionKey) {
      return openSessions.find((entry) => entry.key === activeSessionKey) || null;
    }

    return findCachedRouteEntry(
      openSessions,
      routeSessionKey,
      routeSessionId,
      routeDraft,
      routeNodeId,
      routeProjectName,
      routeProvider,
    );
  }, [
    activeSessionKey,
    openSessions,
    routeDraft,
    routeNodeId,
    routeProjectName,
    routeProvider,
    routeSessionId,
    routeSessionKey,
  ]);

  const selectedDraftProvider =
    selectedOpenSessionEntry?.isDraft && selectedOpenSessionEntry.provider
      ? selectedOpenSessionEntry.provider
      : null;
  const selectedDraftSessionKey = selectedOpenSessionEntry?.isDraft
    ? selectedOpenSessionEntry.key
    : null;

  const sidebarSharedProps = useMemo(
    () => ({
      projects,
      openSessions,
      activeSessionKey,
      availableSessions,
      selectedProject,
      selectedSession,
      onOpenSession: activateOpenSession,
      onCreateDraft: createDraftSession,
      onCloseSession: closeOpenSession,
      isLoading: isLoadingProjects,
      loadingProgress,
      onRefresh: handleSidebarRefresh,
      onShowSettings: () => setShowSettings(true),
      showSettings,
      settingsInitialTab,
      onCloseSettings: () => setShowSettings(false),
      isMobile,
    }),
    [
      activeSessionKey,
      activateOpenSession,
      availableSessions,
      closeOpenSession,
      createDraftSession,
      handleSidebarRefresh,
      isLoadingProjects,
      isMobile,
      loadingProgress,
      openSessions,
      projects,
      selectedProject,
      selectedSession,
      settingsInitialTab,
      showSettings,
    ],
  );

  return {
    projects,
    openSessions,
    activeSessionKey,
    selectedProject,
    selectedSession,
    selectedDraftProvider,
    selectedDraftSessionKey,
    mainTab,
    utilityPanelTab,
    activeTab,
    sidebarOpen,
    isLoadingProjects,
    loadingProgress,
    isInputFocused,
    showSettings,
    settingsInitialTab,
    externalMessageUpdate,
    setActiveTab,
    closeUtilityPanel,
    setSidebarOpen,
    setIsInputFocused,
    setShowSettings,
    openSettings,
    refreshProjectsSilently,
    sidebarSharedProps,
    handleNavigateToSession,
  };
}
