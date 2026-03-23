import { IS_PLATFORM } from "../constants/config";

// Derive API base from the JS module's own URL (immune to client-side routing paths)
const getApiBase = () => {
  const base = document.querySelector('base')?.href;
  if (base) {
    return new URL(base).pathname.replace(/\/+$/, '');
  }

  // import.meta.url points to where this JS was loaded from,
  // e.g. https://host/proxy/rlagent:3000/assets/index-xxx.js
  // Extract the prefix before /assets/ as the app base path.
  // When base is './' and the page is at /proxy/rlagent:3000/session/xxx,
  // import.meta.url becomes .../session/xxx/assets/..., so we must also
  // strip known SPA route segments from the computed base.
  try {
    const moduleUrl = new URL(import.meta.url);
    const assetsIdx = moduleUrl.pathname.indexOf('/assets/');
    if (assetsIdx >= 0) {
      let basePath = moduleUrl.pathname.substring(0, assetsIdx);
      // Strip SPA route segments that leaked into the path due to relative base
      basePath = basePath.replace(/\/session\/[^/]+\/?$/, '');
      basePath = basePath.replace(/\/session\/?$/, '');
      basePath = basePath.replace(/\/index\.html$/, '');
      return basePath.replace(/\/+$/, '');
    }
  } catch {}

  return '';
};

const API_BASE = getApiBase();

// Get the basename for React Router (same logic as API base)
export const getRouterBasename = () => API_BASE || '';

// Prefix a path with the base URL (turns '/api/foo' into '/proxy/rlagent:3000/api/foo')
export const prefixUrl = (url) => {
  if (url.startsWith('/')) {
    return API_BASE + url;
  }
  return url;
};

// Get the WebSocket base URL (e.g. 'wss://host/proxy/rlagent:3000')
export const getWsBase = () => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${API_BASE}`;
};

// Multi-node support: get the selected node ID from localStorage
const getSelectedNodeId = () => localStorage.getItem('selectedNodeId');
const isMultiNodeMode = () => !!getSelectedNodeId();

const withNodeHeader = (options = {}, nodeId = null) => {
  if (!nodeId) {
    return options;
  }

  return {
    ...options,
    headers: {
      ...(options.headers || {}),
      'X-Node-Id': nodeId,
    },
  };
};

// Utility function for authenticated API calls
export const authenticatedFetch = (url, options = {}) => {
  const token = localStorage.getItem('auth-token');

  const defaultHeaders = {};

  // Only set Content-Type for non-FormData requests
  if (!(options.body instanceof FormData)) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  if (!IS_PLATFORM && token) {
    defaultHeaders['Authorization'] = `Bearer ${token}`;
  }

  // Multi-node mode: inject X-Node-Id header for all /api/* requests (except /api/auth and /api/nodes)
  const nodeId = getSelectedNodeId();
  if (nodeId && url.startsWith('/api/') && !url.startsWith('/api/auth') && !url.startsWith('/api/nodes')) {
    defaultHeaders['X-Node-Id'] = nodeId;
  }

  return fetch(prefixUrl(url), {
    ...options,
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  }).then((response) => {
    const refreshedToken = response.headers.get('X-Refreshed-Token');
    if (refreshedToken) {
      localStorage.setItem('auth-token', refreshedToken);
    }
    return response;
  });
};

// API endpoints
export const api = {
  // Auth endpoints (no token required)
  auth: {
    status: () => fetch(prefixUrl('/api/auth/status')),
    login: (username, password) => fetch(prefixUrl('/api/auth/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),
    register: (username, password) => fetch(prefixUrl('/api/auth/register'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),
    user: () => authenticatedFetch('/api/auth/user'),
    logout: () => authenticatedFetch('/api/auth/logout', { method: 'POST' }),
  },

  // Protected endpoints
  // config endpoint removed - no longer needed (frontend uses window.location)
  projects: () => authenticatedFetch('/api/projects'),
  sessions: (projectName, limit = 5, offset = 0, provider = 'claude') => {
    const params = new URLSearchParams();
    params.append('limit', limit);
    params.append('offset', offset);
    if (provider) params.append('provider', provider);
    return authenticatedFetch(`/api/projects/${projectName}/sessions?${params.toString()}`);
  },
  sessionMessages: (projectName, sessionId, limit = null, offset = 0, provider = 'claude') => {
    const params = new URLSearchParams();
    if (limit !== null) {
      params.append('limit', limit);
      params.append('offset', offset);
    }
    const queryString = params.toString();

    let url;
    if (provider === 'codex') {
      url = `/api/codex/sessions/${sessionId}/messages${queryString ? `?${queryString}` : ''}`;
    } else {
      url = `/api/projects/${projectName}/sessions/${sessionId}/messages${queryString ? `?${queryString}` : ''}`;
    }
    return authenticatedFetch(url);
  },
  renameProject: (projectName, displayName) =>
    authenticatedFetch(`/api/projects/${projectName}/rename`, {
      method: 'PUT',
      body: JSON.stringify({ displayName }),
    }),
  deleteSession: (projectName, sessionId) =>
    authenticatedFetch(`/api/projects/${projectName}/sessions/${sessionId}`, {
      method: 'DELETE',
    }),
  renameSession: (sessionId, summary, provider) =>
    authenticatedFetch(`/api/sessions/${sessionId}/rename`, {
      method: 'PUT',
      body: JSON.stringify({ summary, provider }),
    }),
  deleteCodexSession: (sessionId) =>
    authenticatedFetch(`/api/codex/sessions/${sessionId}`, {
      method: 'DELETE',
    }),
  deleteProject: (projectName, force = false) =>
    authenticatedFetch(`/api/projects/${projectName}${force ? '?force=true' : ''}`, {
      method: 'DELETE',
    }),
  searchConversationsUrl: (query, limit = 50) => {
    const token = localStorage.getItem('auth-token');
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (token) params.set('token', token);
    return `/api/search/conversations?${params.toString()}`;
  },
  /**
   * @param {string} path
   * @param {string | null} [nodeId]
   */
  createProject: (path, nodeId = null) =>
    authenticatedFetch(
      '/api/projects/create',
      withNodeHeader(
        {
          method: 'POST',
          body: JSON.stringify({ path }),
        },
        nodeId,
      ),
    ),
  createWorkspace: (workspaceData) =>
    authenticatedFetch('/api/projects/create-workspace', {
      method: 'POST',
      body: JSON.stringify(workspaceData),
    }),
  readFile: (projectName, filePath) =>
    authenticatedFetch(`/api/projects/${projectName}/file?filePath=${encodeURIComponent(filePath)}`),
  saveFile: (projectName, filePath, content) =>
    authenticatedFetch(`/api/projects/${projectName}/file`, {
      method: 'PUT',
      body: JSON.stringify({ filePath, content }),
    }),
  getFiles: (projectName, options = {}) =>
    authenticatedFetch(`/api/projects/${projectName}/files`, options),

  // File operations
  createFile: (projectName, { path, type, name }) =>
    authenticatedFetch(`/api/projects/${projectName}/files/create`, {
      method: 'POST',
      body: JSON.stringify({ path, type, name }),
    }),

  renameFile: (projectName, { oldPath, newName }) =>
    authenticatedFetch(`/api/projects/${projectName}/files/rename`, {
      method: 'PUT',
      body: JSON.stringify({ oldPath, newName }),
    }),

  deleteFile: (projectName, { path, type }) =>
    authenticatedFetch(`/api/projects/${projectName}/files`, {
      method: 'DELETE',
      body: JSON.stringify({ path, type }),
    }),

  uploadFiles: (projectName, formData) =>
    authenticatedFetch(`/api/projects/${projectName}/files/upload`, {
      method: 'POST',
      body: formData,
      headers: {}, // Let browser set Content-Type for FormData
    }),

  transcribe: (formData) =>
    authenticatedFetch('/api/transcribe', {
      method: 'POST',
      body: formData,
      headers: {}, // Let browser set Content-Type for FormData
    }),

  // TaskMaster endpoints
  taskmaster: {
    // Initialize TaskMaster in a project
    init: (projectName) =>
      authenticatedFetch(`/api/taskmaster/init/${projectName}`, {
        method: 'POST',
      }),

    // Add a new task
    addTask: (projectName, { prompt, title, description, priority, dependencies }) =>
      authenticatedFetch(`/api/taskmaster/add-task/${projectName}`, {
        method: 'POST',
        body: JSON.stringify({ prompt, title, description, priority, dependencies }),
      }),

    // Parse PRD to generate tasks
    parsePRD: (projectName, { fileName, numTasks, append }) =>
      authenticatedFetch(`/api/taskmaster/parse-prd/${projectName}`, {
        method: 'POST',
        body: JSON.stringify({ fileName, numTasks, append }),
      }),

    // Get available PRD templates
    getTemplates: () =>
      authenticatedFetch('/api/taskmaster/prd-templates'),

    // Apply a PRD template
    applyTemplate: (projectName, { templateId, fileName, customizations }) =>
      authenticatedFetch(`/api/taskmaster/apply-template/${projectName}`, {
        method: 'POST',
        body: JSON.stringify({ templateId, fileName, customizations }),
      }),

    // Update a task
    updateTask: (projectName, taskId, updates) =>
      authenticatedFetch(`/api/taskmaster/update-task/${projectName}/${taskId}`, {
        method: 'PUT',
        body: JSON.stringify(updates),
      }),
  },

  // Browse filesystem for project suggestions
  /**
   * @param {string | null} [dirPath]
   * @param {string | null} [nodeId]
   */
  browseFilesystem: (dirPath = null, nodeId = null) => {
    const params = new URLSearchParams();
    if (dirPath) params.append('path', dirPath);
    const queryString = params.toString();

    return authenticatedFetch(
      queryString ? `/api/browse-filesystem?${queryString}` : '/api/browse-filesystem',
      withNodeHeader({}, nodeId),
    );
  },

  createFolder: (folderPath) =>
    authenticatedFetch('/api/create-folder', {
      method: 'POST',
      body: JSON.stringify({ path: folderPath }),
    }),

  // User endpoints
  user: {
    gitConfig: () => authenticatedFetch('/api/user/git-config'),
    updateGitConfig: (gitName, gitEmail) =>
      authenticatedFetch('/api/user/git-config', {
        method: 'POST',
        body: JSON.stringify({ gitName, gitEmail }),
      }),
    onboardingStatus: () => authenticatedFetch('/api/user/onboarding-status'),
    completeOnboarding: () =>
      authenticatedFetch('/api/user/complete-onboarding', {
        method: 'POST',
      }),
  },

  // Generic GET method for any endpoint
  get: (endpoint) => authenticatedFetch(`/api${endpoint}`),

  // Generic POST method for any endpoint
  post: (endpoint, body) => authenticatedFetch(`/api${endpoint}`, {
    method: 'POST',
    ...(body instanceof FormData ? { body } : { body: JSON.stringify(body) }),
  }),

  // Generic PUT method for any endpoint
  put: (endpoint, body) => authenticatedFetch(`/api${endpoint}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }),

  // Generic DELETE method for any endpoint
  delete: (endpoint, options = {}) => authenticatedFetch(`/api${endpoint}`, {
    method: 'DELETE',
    ...options,
  }),

  // Multi-node support
  nodes: {
    list: () => authenticatedFetch('/api/nodes'),
    get: (nodeId) => authenticatedFetch(`/api/nodes/${nodeId}`),
    delete: (nodeId) =>
      authenticatedFetch(`/api/nodes/${nodeId}`, {
        method: 'DELETE',
      }),
    projects: (nodeId) => authenticatedFetch(`/api/nodes/${nodeId}/projects`),
    sessions: (nodeId, projectName, limit = 5, offset = 0, provider = 'claude') => {
      const params = new URLSearchParams();
      params.append('limit', limit);
      params.append('offset', offset);
      if (provider) params.append('provider', provider);
      return authenticatedFetch(
        `/api/nodes/${nodeId}/projects/${projectName}/sessions?${params.toString()}`
      );
    },
    sessionMessages: (nodeId, projectName, sessionId, limit = null, offset = 0, provider = 'claude') => {
      const params = new URLSearchParams();
      if (limit !== null) {
        params.append('limit', limit);
        params.append('offset', offset);
      }
      if (provider) params.append('provider', provider);
      const queryString = params.toString();
      return authenticatedFetch(
        `/api/nodes/${nodeId}/projects/${projectName}/sessions/${sessionId}/messages${queryString ? `?${queryString}` : ''}`
      );
    },
    ping: (nodeId) => authenticatedFetch(`/api/nodes/${nodeId}/ping`),
  },

  // Node-aware wrappers: automatically route through node if in multi-node mode
  nodeAwareProjects: () => {
    const nodeId = getSelectedNodeId();
    if (nodeId && isMultiNodeMode()) {
      return authenticatedFetch(`/api/nodes/${nodeId}/projects`);
    }
    return authenticatedFetch('/api/projects');
  },
  nodeAwareSessions: (projectName, limit = 5, offset = 0, provider = 'claude') => {
    const nodeId = getSelectedNodeId();
    const params = new URLSearchParams();
    params.append('limit', limit);
    params.append('offset', offset);
    if (provider) params.append('provider', provider);
    const queryString = params.toString();
    if (nodeId && isMultiNodeMode()) {
      return authenticatedFetch(`/api/nodes/${nodeId}/projects/${projectName}/sessions?${queryString}`);
    }
    return authenticatedFetch(`/api/projects/${projectName}/sessions?${queryString}`);
  },
  nodeAwareSessionMessages: (projectName, sessionId, limit = null, offset = 0, provider = 'claude') => {
    const nodeId = getSelectedNodeId();
    if (nodeId && isMultiNodeMode()) {
      const params = new URLSearchParams();
      if (limit !== null) {
        params.append('limit', limit);
        params.append('offset', offset);
      }
      if (provider) params.append('provider', provider);
      const queryString = params.toString();
      return authenticatedFetch(
        `/api/nodes/${nodeId}/projects/${projectName}/sessions/${sessionId}/messages${queryString ? `?${queryString}` : ''}`
      );
    }
    // Fall back to original provider-aware routing
    return api.sessionMessages(projectName, sessionId, limit, offset, provider);
  },
};
