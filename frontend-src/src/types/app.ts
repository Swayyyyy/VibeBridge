export type SessionProvider = 'claude' | 'codex';

export type UtilityPanelTab = 'files' | 'shell' | 'git';
export type MainAreaTab = 'chat' | 'tasks' | 'preview' | `plugin:${string}`;
export type AppTab = MainAreaTab | UtilityPanelTab;

export interface ProjectSession {
  id: string;
  title?: string;
  summary?: string;
  name?: string;
  createdAt?: string;
  created_at?: string;
  updated_at?: string;
  lastActivity?: string;
  messageCount?: number;
  __provider?: SessionProvider;
  __projectName?: string;
  __projectDisplayName?: string;
  __projectPath?: string;
  __nodeId?: string | null;
  __nodeDisplayName?: string | null;
  __openSessionKey?: string;
  __isDraft?: boolean;
  [key: string]: unknown;
}

export interface ProjectSessionMeta {
  total?: number;
  hasMore?: boolean;
  [key: string]: unknown;
}

export interface ProjectTaskmasterInfo {
  hasTaskmaster?: boolean;
  status?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Project {
  name: string;
  displayName: string;
  fullPath: string;
  path?: string;
  nodeId?: string | null;
  nodeDisplayName?: string | null;
  sessions?: ProjectSession[];
  codexSessions?: ProjectSession[];
  sessionMeta?: ProjectSessionMeta;
  taskmaster?: ProjectTaskmasterInfo;
  [key: string]: unknown;
}

export interface OpenSessionEntry {
  key: string;
  sessionId: string | null;
  title: string;
  provider?: SessionProvider;
  projectName: string;
  projectDisplayName: string;
  projectPath: string;
  nodeId?: string | null;
  nodeDisplayName?: string | null;
  createdAt?: string;
  updatedAt?: string;
  isDraft?: boolean;
  lastOpenedAt: string;
}

export interface LoadingProgress {
  type?: 'loading_progress';
  phase?: string;
  current: number;
  total: number;
  currentProject?: string;
  [key: string]: unknown;
}

export interface ProjectsUpdatedMessage {
  type: 'projects_updated';
  projects: Project[];
  changedFile?: string;
  [key: string]: unknown;
}

export interface LoadingProgressMessage extends LoadingProgress {
  type: 'loading_progress';
}

export type AppSocketMessage =
  | LoadingProgressMessage
  | ProjectsUpdatedMessage
  | { type?: string;[key: string]: unknown };
