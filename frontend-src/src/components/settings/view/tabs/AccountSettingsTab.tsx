import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '../../../../shared/view/ui';
import { authenticatedFetch } from '../../../../utils/api';
import { copyTextToClipboard } from '../../../../utils/clipboard';

type AccountProfile = {
  id: number;
  username: string;
  role: 'creator' | 'admin' | 'user' | 'pending';
  nodeRegisterToken: string | null;
};

type ManagedUser = {
  id: number;
  username: string;
  role: 'creator' | 'admin' | 'user' | 'pending';
};

export default function AccountSettingsTab() {
  const { t } = useTranslation('settings');
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const profileResponse = await authenticatedFetch('/api/account/profile');
      if (!profileResponse.ok) {
        throw new Error('Failed to load profile');
      }
      const profilePayload = await profileResponse.json();
      const nextProfile = profilePayload.user as AccountProfile;
      setProfile(nextProfile);

      if (nextProfile.role === 'creator' || nextProfile.role === 'admin') {
        const usersResponse = await authenticatedFetch('/api/admin/users');
        if (!usersResponse.ok) {
          throw new Error('Failed to load users');
        }
        const usersPayload = await usersResponse.json();
        setUsers(Array.isArray(usersPayload.users) ? usersPayload.users : []);
      } else {
        setUsers([]);
      }
    } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : 'Failed to load account settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const copyToken = useCallback(async (token: string | null | undefined) => {
    if (!token) {
      return;
    }

    const copied = await copyTextToClipboard(token);
    if (!copied) {
      setError('Failed to copy token');
    }
  }, []);

  const rotateOwnToken = useCallback(async () => {
    setBusyKey('self-token');
    setError(null);
    try {
      const response = await authenticatedFetch('/api/account/node-register-token/rotate', {
        method: 'POST',
      });
      if (!response.ok) {
        throw new Error('Failed to rotate node token');
      }
      await load();
    } catch (rotateError) {
      setError(rotateError instanceof Error ? rotateError.message : 'Failed to rotate node token');
    } finally {
      setBusyKey(null);
    }
  }, [load]);

  const updateUserRole = useCallback(async (userId: number, role: 'admin' | 'user' | 'pending') => {
    setBusyKey(`role-${userId}`);
    setError(null);
    try {
      const response = await authenticatedFetch(`/api/admin/users/${userId}/role`, {
        method: 'POST',
        body: JSON.stringify({ role }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || 'Failed to update role');
      }
      await load();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : 'Failed to update role');
    } finally {
      setBusyKey(null);
    }
  }, [load]);

  const approveUser = useCallback(async (userId: number) => {
    setBusyKey(`approve-${userId}`);
    setError(null);
    try {
      const response = await authenticatedFetch(`/api/admin/users/${userId}/approve`, {
        method: 'POST',
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || 'Failed to approve user');
      }
      await load();
    } catch (approveError) {
      setError(approveError instanceof Error ? approveError.message : 'Failed to approve user');
    } finally {
      setBusyKey(null);
    }
  }, [load]);

  const deleteUser = useCallback(async (userId: number) => {
    setBusyKey(`delete-${userId}`);
    setError(null);
    try {
      const response = await authenticatedFetch(`/api/admin/users/${userId}`, {
        method: 'DELETE',
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || 'Failed to delete user');
      }
      await load();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'Failed to delete user');
    } finally {
      setBusyKey(null);
    }
  }, [load]);

  const canDeleteUser = useCallback((user: ManagedUser) => {
    if (!profile) {
      return false;
    }
    if (user.id === profile.id) {
      return false;
    }
    if (profile.role === 'creator') {
      return true;
    }
    if (profile.role === 'admin') {
      return user.role === 'user';
    }
    return false;
  }, [profile]);

  return (
    <div className="space-y-6">
      <section className="space-y-3 rounded-lg border border-border p-4">
        <div>
          <h3 className="text-base font-semibold text-foreground">
            {t('accountSettings.title', { defaultValue: 'Account & Node Token' })}
          </h3>
          <p className="text-sm text-muted-foreground">
            {t('accountSettings.description', { defaultValue: 'Each user gets a dedicated node registration token.' })}
          </p>
        </div>

        {loading ? (
          <p className="text-sm text-muted-foreground">{t('accountSettings.loading', { defaultValue: 'Loading…' })}</p>
        ) : profile ? (
          <div className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-md border border-border bg-muted/30 p-3">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t('accountSettings.username', { defaultValue: 'Username' })}
                </div>
                <div className="mt-1 text-sm font-medium text-foreground">{profile.username}</div>
              </div>
              <div className="rounded-md border border-border bg-muted/30 p-3">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t('accountSettings.role', { defaultValue: 'Role' })}
                </div>
                <div className="mt-1 text-sm font-medium text-foreground">{profile.role}</div>
              </div>
            </div>

            {profile.role === 'pending' && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
                {t('accountSettings.pendingNotice', { defaultValue: 'Your account is waiting for approval from an admin or the creator.' })}
              </div>
            )}

            <div className="rounded-md border border-border bg-muted/30 p-3">
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                {t('accountSettings.nodeToken', { defaultValue: 'Node Register Token' })}
              </div>
              <div className="mt-2 break-all rounded bg-background px-3 py-2 font-mono text-xs text-foreground">
                {profile.nodeRegisterToken || '-'}
              </div>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void copyToken(profile.nodeRegisterToken)}
                  disabled={!profile.nodeRegisterToken}
                >
                  {t('accountSettings.copy', { defaultValue: 'Copy' })}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void rotateOwnToken()}
                  disabled={busyKey === 'self-token' || profile.role === 'pending'}
                >
                  {t('accountSettings.rotate', { defaultValue: 'Rotate Token' })}
                </Button>
              </div>
            </div>
          </div>
        ) : null}
      </section>

      {(profile?.role === 'creator' || profile?.role === 'admin') && (
        <section className="space-y-3 rounded-lg border border-border p-4">
          <div>
            <h3 className="text-base font-semibold text-foreground">
              {t('accountSettings.userAdminTitle', { defaultValue: 'User Management' })}
            </h3>
            <p className="text-sm text-muted-foreground">
              {profile.role === 'creator'
                ? t('accountSettings.creatorDescription', { defaultValue: 'Creators can manage roles and delete any other user.' })
                : t('accountSettings.adminDescription', { defaultValue: 'Admins can approve pending users and delete normal users.' })}
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4">{t('accountSettings.username', { defaultValue: 'Username' })}</th>
                  <th className="py-2 pr-4">{t('accountSettings.role', { defaultValue: 'Role' })}</th>
                  <th className="py-2">{t('accountSettings.actions', { defaultValue: 'Actions' })}</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-b border-border/60 align-top">
                    <td className="py-3 pr-4 text-foreground">{user.username}</td>
                    <td className="py-3 pr-4">
                      {profile.role === 'creator' && user.role !== 'creator' ? (
                        <select
                          className="h-9 min-w-[8rem] appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                          value={user.role}
                          onChange={(event) => void updateUserRole(user.id, event.target.value as 'admin' | 'user' | 'pending')}
                          disabled={busyKey === `role-${user.id}`}
                        >
                          <option value="admin">admin</option>
                          <option value="user">user</option>
                          <option value="pending">pending</option>
                        </select>
                      ) : profile.role === 'admin' && user.role === 'pending' ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void approveUser(user.id)}
                          disabled={busyKey === `approve-${user.id}`}
                        >
                          {t('accountSettings.approve', { defaultValue: 'Approve' })}
                        </Button>
                      ) : (
                        <span className="text-sm text-foreground">{user.role}</span>
                      )}
                    </td>
                    <td className="py-3">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void deleteUser(user.id)}
                        disabled={!canDeleteUser(user) || busyKey === `delete-${user.id}`}
                      >
                        {t('accountSettings.delete', { defaultValue: 'Delete' })}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}
    </div>
  );
}
