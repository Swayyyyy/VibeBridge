import { AUTH_TOKEN_STORAGE_KEY, AUTH_USER_ID_STORAGE_KEY } from '../components/auth/constants';
import { prefixUrl } from './api';

const USER_PREFIX = 'user';
const GLOBAL_KEYS = new Set([AUTH_TOKEN_STORAGE_KEY, AUTH_USER_ID_STORAGE_KEY]);

let installed = false;
let suspendPersistence = 0;
let pendingUserId = '';
let pendingUpdates = new Map<string, string | null>();
let flushTimer: number | null = null;
const hydratedUsers = new Set<string>();

const PERSISTENCE_SKIP_PREFIXES = ['chat_messages_', 'draft_input_'];

let originalGetItem: ((this: Storage, key: string) => string | null) | null = null;
let originalSetItem: ((this: Storage, key: string, value: string) => void) | null = null;
let originalRemoveItem: ((this: Storage, key: string) => void) | null = null;
let originalClear: ((this: Storage) => void) | null = null;

const getStoragePrototype = (): Storage | null => {
  if (typeof window === 'undefined') {
    return null;
  }

  return Object.getPrototypeOf(window.localStorage) as Storage;
};

const getActiveUserId = (): string => {
  if (typeof window === 'undefined' || !originalGetItem) {
    return '';
  }

  return originalGetItem.call(window.localStorage, AUTH_USER_ID_STORAGE_KEY) || '';
};

const shouldNamespaceKey = (key: string): boolean => {
  if (!key || GLOBAL_KEYS.has(key)) {
    return false;
  }

  return !key.startsWith(`${USER_PREFIX}:`);
};

export const buildScopedStorageKey = (userId: string, key: string): string => (
  `${USER_PREFIX}:${userId}:${key}`
);

export const getScopedStoragePrefix = (): string => {
  const userId = getActiveUserId();
  return userId ? `${USER_PREFIX}:${userId}:` : '';
};

export const listCurrentUserStorageKeys = (storage: Storage = localStorage): string[] => {
  const prefix = getScopedStoragePrefix();
  const keys = Object.keys(storage);

  if (!prefix) {
    return keys;
  }

  return keys.filter((key) => key.startsWith(prefix));
};

const shouldPersistKey = (key: string): boolean => {
  if (!shouldNamespaceKey(key)) {
    return false;
  }

  return !PERSISTENCE_SKIP_PREFIXES.some((prefix) => key.startsWith(prefix));
};

const clearPendingUpdates = () => {
  pendingUpdates.clear();
  pendingUserId = '';
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
};

const flushPendingUpdates = async () => {
  if (!pendingUserId || pendingUpdates.size === 0 || !originalGetItem || typeof window === 'undefined') {
    clearPendingUpdates();
    return;
  }

  const token = originalGetItem.call(window.localStorage, AUTH_TOKEN_STORAGE_KEY);
  const body = Object.fromEntries(pendingUpdates);
  clearPendingUpdates();

  if (!token) {
    return;
  }

  try {
    await fetch(prefixUrl('/api/user/preferences'), {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ settings: body }),
    });
  } catch (error) {
    console.error('Failed to persist user-scoped preferences:', error);
  }
};

const enqueuePreferenceUpdate = (userId: string, key: string, value: string | null) => {
  if (!userId || suspendPersistence > 0 || !hydratedUsers.has(userId)) {
    return;
  }

  if (pendingUserId && pendingUserId !== userId) {
    clearPendingUpdates();
  }

  pendingUserId = userId;
  pendingUpdates.set(key, value);

  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
  }

  flushTimer = window.setTimeout(() => {
    void flushPendingUpdates();
  }, 300);
};

export const hydrateUserScopedStorage = (userId: string, settings: Record<string, string>) => {
  if (!userId || !originalSetItem || !originalRemoveItem || typeof window === 'undefined') {
    return;
  }

  suspendPersistence += 1;
  try {
    for (const [key, value] of Object.entries(settings || {})) {
      if (!shouldNamespaceKey(key)) {
        continue;
      }
      originalSetItem.call(window.localStorage, buildScopedStorageKey(userId, key), String(value));
      originalRemoveItem.call(window.localStorage, key);
    }
    hydratedUsers.add(userId);
  } finally {
    suspendPersistence -= 1;
  }
};

export const markUserScopedStorageHydrated = (userId: string) => {
  if (userId) {
    hydratedUsers.add(userId);
  }
};

export const resetUserScopedStorageHydration = (userId?: string) => {
  const resolvedUserId = userId || getActiveUserId();
  if (resolvedUserId) {
    hydratedUsers.delete(resolvedUserId);
  }
  if (!resolvedUserId || pendingUserId === resolvedUserId) {
    clearPendingUpdates();
  }
};

export const installUserScopedStorage = () => {
  if (installed) {
    return;
  }

  const storageProto = getStoragePrototype();
  if (!storageProto) {
    return;
  }

  originalGetItem = storageProto.getItem;
  originalSetItem = storageProto.setItem;
  originalRemoveItem = storageProto.removeItem;
  originalClear = storageProto.clear;

  storageProto.getItem = function getItemPatched(key: string): string | null {
    const normalizedKey = String(key);
    if (!originalGetItem) {
      return null;
    }

    if (!shouldNamespaceKey(normalizedKey)) {
      return originalGetItem.call(this, normalizedKey);
    }

    const userId = getActiveUserId();
    if (!userId) {
      return originalGetItem.call(this, normalizedKey);
    }

    const scopedKey = buildScopedStorageKey(userId, normalizedKey);
    return originalGetItem.call(this, scopedKey);
  };

  storageProto.setItem = function setItemPatched(key: string, value: string): void {
    const normalizedKey = String(key);
    if (!originalSetItem || !originalRemoveItem) {
      return;
    }

    if (!shouldNamespaceKey(normalizedKey)) {
      originalSetItem.call(this, normalizedKey, value);
      return;
    }

    const userId = getActiveUserId();
    if (!userId) {
      originalSetItem.call(this, normalizedKey, value);
      return;
    }

    const scopedKey = buildScopedStorageKey(userId, normalizedKey);
    originalSetItem.call(this, scopedKey, value);
    originalRemoveItem.call(this, normalizedKey);
    if (this === window.localStorage && shouldPersistKey(normalizedKey)) {
      enqueuePreferenceUpdate(userId, normalizedKey, value);
    }
  };

  storageProto.removeItem = function removeItemPatched(key: string): void {
    const normalizedKey = String(key);
    if (!originalRemoveItem) {
      return;
    }

    if (!shouldNamespaceKey(normalizedKey)) {
      originalRemoveItem.call(this, normalizedKey);
      return;
    }

    const userId = getActiveUserId();
    if (!userId) {
      originalRemoveItem.call(this, normalizedKey);
      return;
    }

    originalRemoveItem.call(this, buildScopedStorageKey(userId, normalizedKey));
    originalRemoveItem.call(this, normalizedKey);
    if (this === window.localStorage && shouldPersistKey(normalizedKey)) {
      enqueuePreferenceUpdate(userId, normalizedKey, null);
    }
  };

  storageProto.clear = function clearPatched(): void {
    if (!originalClear || !originalRemoveItem) {
      return;
    }

    const userId = getActiveUserId();
    if (!userId) {
      originalClear.call(this);
      return;
    }

    const prefix = buildScopedStorageKey(userId, '');
    Object.keys(this)
      .filter((key) => key.startsWith(prefix))
      .forEach((key) => originalRemoveItem!.call(this, key));
  };

  installed = true;
};
