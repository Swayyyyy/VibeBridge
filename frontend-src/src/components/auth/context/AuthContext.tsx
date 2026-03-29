import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { IS_PLATFORM } from '../../../constants/config';
import { api, prefixUrl } from '../../../utils/api';
import { AUTH_ERROR_MESSAGES, AUTH_TOKEN_STORAGE_KEY, AUTH_USER_ID_STORAGE_KEY } from '../constants';
import type {
  AuthContextValue,
  AuthProviderProps,
  AuthSessionPayload,
  AuthStatusPayload,
  AuthUser,
  AuthUserPayload,
  OnboardingStatusPayload,
} from '../types';
import { parseJsonSafely, resolveApiErrorMessage } from '../utils';
import {
  hydrateUserScopedStorage,
  markUserScopedStorageHydrated,
  resetUserScopedStorageHydration,
} from '../../../utils/userScopedStorage';

const AuthContext = createContext<AuthContextValue | null>(null);

const readStoredToken = (): string | null => localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);

const persistToken = (token: string) => {
  localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
};

const persistUserId = (user: AuthUser) => {
  if (user.id === undefined || user.id === null) {
    localStorage.removeItem(AUTH_USER_ID_STORAGE_KEY);
    return;
  }

  localStorage.setItem(AUTH_USER_ID_STORAGE_KEY, String(user.id));
};

const clearStoredToken = () => {
  localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
};

const clearStoredUserId = () => {
  localStorage.removeItem(AUTH_USER_ID_STORAGE_KEY);
};

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }

  return context;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(() => readStoredToken());
  const [isLoading, setIsLoading] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [hasCompletedOnboarding, setHasCompletedOnboarding] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const hydrateUserPreferences = useCallback(async (nextUser: AuthUser, nextToken: string) => {
    const userId = nextUser.id === undefined || nextUser.id === null ? '' : String(nextUser.id);
    if (!userId || !nextToken) {
      return;
    }

    try {
      const response = await fetch(prefixUrl('/api/user/preferences'), {
        headers: {
          Authorization: `Bearer ${nextToken}`,
        },
      });
      if (response.ok) {
        const payload = await parseJsonSafely<{ settings?: Record<string, string> }>(response);
        hydrateUserScopedStorage(userId, payload?.settings || {});
      }
    } catch (error) {
      console.error('Failed to hydrate user preferences:', error);
    } finally {
      markUserScopedStorageHydrated(userId);
    }
  }, []);

  const setSession = useCallback((nextUser: AuthUser, nextToken: string) => {
    setUser(nextUser);
    setToken(nextToken);
    persistToken(nextToken);
    persistUserId(nextUser);
  }, []);

  const clearSession = useCallback(() => {
    const currentUserId = user?.id === undefined || user?.id === null ? '' : String(user.id);
    resetUserScopedStorageHydration(currentUserId);
    setUser(null);
    setToken(null);
    clearStoredToken();
    clearStoredUserId();
  }, [user]);

  const checkOnboardingStatus = useCallback(async () => {
    try {
      const response = await api.user.onboardingStatus();
      if (!response.ok) {
        return;
      }

      const payload = await parseJsonSafely<OnboardingStatusPayload>(response);
      setHasCompletedOnboarding(Boolean(payload?.hasCompletedOnboarding));
    } catch (caughtError) {
      console.error('Error checking onboarding status:', caughtError);
      // Fail open to avoid blocking access on transient onboarding status errors.
      setHasCompletedOnboarding(true);
    }
  }, []);

  const refreshOnboardingStatus = useCallback(async () => {
    await checkOnboardingStatus();
  }, [checkOnboardingStatus]);

  const checkAuthStatus = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const statusResponse = await api.auth.status();
      const statusPayload = await parseJsonSafely<AuthStatusPayload>(statusResponse);

      if (statusPayload?.needsSetup) {
        setNeedsSetup(true);
        return;
      }

      setNeedsSetup(false);

      if (!token) {
        return;
      }

      const userResponse = await api.auth.user();
      if (!userResponse.ok) {
        clearSession();
        return;
      }

      const userPayload = await parseJsonSafely<AuthUserPayload>(userResponse);
      if (!userPayload?.user) {
        clearSession();
        return;
      }

      persistUserId(userPayload.user);
      await hydrateUserPreferences(userPayload.user, token);
      setUser(userPayload.user);
      await checkOnboardingStatus();
    } catch (caughtError) {
      console.error('[Auth] Auth status check failed:', caughtError);
      setError(AUTH_ERROR_MESSAGES.authStatusCheckFailed);
    } finally {
      setIsLoading(false);
    }
  }, [checkOnboardingStatus, clearSession, hydrateUserPreferences, token]);

  useEffect(() => {
    if (IS_PLATFORM) {
      setUser({ username: 'platform-user' });
      setNeedsSetup(false);
      void checkOnboardingStatus().finally(() => {
        setIsLoading(false);
      });
      return;
    }

    void checkAuthStatus();
  }, [checkAuthStatus, checkOnboardingStatus]);

  const login = useCallback<AuthContextValue['login']>(
    async (username, password) => {
      try {
        setError(null);
        const response = await api.auth.login(username, password);
        const payload = await parseJsonSafely<AuthSessionPayload>(response);

        if (!response.ok || !payload?.token || !payload.user) {
          const message = resolveApiErrorMessage(payload, AUTH_ERROR_MESSAGES.loginFailed);
          setError(message);
          return { success: false, error: message };
        }

        setSession(payload.user, payload.token);
        await hydrateUserPreferences(payload.user, payload.token);
        setNeedsSetup(false);
        await checkOnboardingStatus();
        return { success: true };
      } catch (caughtError) {
        console.error('Login error:', caughtError);
        setError(AUTH_ERROR_MESSAGES.networkError);
        return { success: false, error: AUTH_ERROR_MESSAGES.networkError };
      }
    },
    [checkOnboardingStatus, hydrateUserPreferences, setSession],
  );

  const register = useCallback<AuthContextValue['register']>(
    async (username, password) => {
      try {
        setError(null);
        const response = await api.auth.register(username, password);
        const payload = await parseJsonSafely<AuthSessionPayload>(response);

        if (!response.ok || !payload?.user) {
          const message = resolveApiErrorMessage(payload, AUTH_ERROR_MESSAGES.registrationFailed);
          setError(message);
          return { success: false, error: message };
        }

        if (payload.token) {
          setSession(payload.user, payload.token);
          await hydrateUserPreferences(payload.user, payload.token);
          setNeedsSetup(false);
          await checkOnboardingStatus();
          return { success: true, message: payload.message || undefined };
        }

        setNeedsSetup(false);
        clearSession();
        return {
          success: true,
          message: payload.message || 'Registration submitted and awaiting approval.',
          pendingApproval: Boolean(payload.pendingApproval),
        };
      } catch (caughtError) {
        console.error('Registration error:', caughtError);
        setError(AUTH_ERROR_MESSAGES.networkError);
        return { success: false, error: AUTH_ERROR_MESSAGES.networkError };
      }
    },
    [checkOnboardingStatus, clearSession, hydrateUserPreferences, setSession],
  );

  const logout = useCallback(() => {
    const tokenToInvalidate = token;
    clearSession();

    if (tokenToInvalidate) {
      void api.auth.logout().catch((caughtError: unknown) => {
        console.error('Logout endpoint error:', caughtError);
      });
    }
  }, [clearSession, token]);

  const contextValue = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isLoading,
      needsSetup,
      hasCompletedOnboarding,
      error,
      login,
      register,
      logout,
      refreshOnboardingStatus,
    }),
    [
      error,
      hasCompletedOnboarding,
      isLoading,
      login,
      logout,
      needsSetup,
      refreshOnboardingStatus,
      register,
      token,
      user,
    ],
  );

  return <AuthContext.Provider value={contextValue}>{children}</AuthContext.Provider>;
}
