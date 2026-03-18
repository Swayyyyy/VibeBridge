import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getStoredClaudeSettings,
  getStoredCodexSettings,
  PROVIDER_SETTINGS_CHANGED_EVENT,
  saveStoredClaudeSettings,
  saveStoredCodexSettings,
  type ProviderThinkingEffort,
} from '../../../shared/providerSettings';
import type { PendingPermissionRequest, PermissionMode } from '../types/types';
import type { ProjectSession, SessionProvider } from '../../../types/app';

interface UseChatProviderStateArgs {
  selectedSession: ProjectSession | null;
  selectedDraftProvider: SessionProvider | null;
  selectedDraftSessionKey: string | null;
}

const readStoredProvider = (): SessionProvider => {
  if (typeof window === 'undefined') {
    return 'claude';
  }

  const storedProvider = window.localStorage.getItem('selected-provider');
  return storedProvider === 'codex' ? 'codex' : 'claude';
};

const readSessionPermissionMode = (sessionId: string | null | undefined): PermissionMode | null => {
  if (!sessionId || typeof window === 'undefined') {
    return null;
  }

  const savedMode = window.localStorage.getItem(`permissionMode-${sessionId}`);
  if (
    savedMode === 'default' ||
    savedMode === 'acceptEdits' ||
    savedMode === 'bypassPermissions' ||
    savedMode === 'plan'
  ) {
    return savedMode;
  }

  return null;
};

const resolvePermissionMode = (
  provider: SessionProvider,
  sessionId: string | null | undefined,
): PermissionMode => {
  const sessionMode = readSessionPermissionMode(sessionId);
  if (sessionMode) {
    return sessionMode;
  }

  if (provider === 'codex') {
    return getStoredCodexSettings().permissionMode;
  }

  return 'default';
};

export function useChatProviderState({
  selectedSession,
  selectedDraftProvider,
  selectedDraftSessionKey,
}: UseChatProviderStateArgs) {
  const [pendingPermissionRequests, setPendingPermissionRequests] = useState<PendingPermissionRequest[]>([]);
  const [provider, setProviderState] = useState<SessionProvider>(() => readStoredProvider());
  const [permissionMode, setPermissionMode] = useState<PermissionMode>(() => (
    resolvePermissionMode(readStoredProvider(), selectedSession?.id)
  ));
  const [claudeModelState, setClaudeModelState] = useState<string>(() => getStoredClaudeSettings().model);
  const [claudeThinkingEffortState, setClaudeThinkingEffortState] = useState<ProviderThinkingEffort>(() => (
    getStoredClaudeSettings().thinkingEffort
  ));
  const [codexModelState, setCodexModelState] = useState<string>(() => getStoredCodexSettings().model);
  const [codexReasoningEffortState, setCodexReasoningEffortState] = useState<ProviderThinkingEffort>(() => (
    getStoredCodexSettings().reasoningEffort
  ));

  const lastProviderRef = useRef(provider);
  const lastDraftSyncKeyRef = useRef<string | null>(null);

  const syncProviderSettingsFromStorage = useCallback(() => {
    const savedClaudeSettings = getStoredClaudeSettings();
    setClaudeModelState(savedClaudeSettings.model);
    setClaudeThinkingEffortState(savedClaudeSettings.thinkingEffort);

    const savedCodexSettings = getStoredCodexSettings();
    setCodexModelState(savedCodexSettings.model);
    setCodexReasoningEffortState(savedCodexSettings.reasoningEffort);
  }, []);

  useEffect(() => {
    syncProviderSettingsFromStorage();
  }, [syncProviderSettingsFromStorage]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const handleProviderSettingsChanged = () => {
      syncProviderSettingsFromStorage();

      if (!selectedSession?.id && provider === 'codex') {
        setPermissionMode(getStoredCodexSettings().permissionMode);
      }
    };

    window.addEventListener(PROVIDER_SETTINGS_CHANGED_EVENT, handleProviderSettingsChanged);
    return () => {
      window.removeEventListener(PROVIDER_SETTINGS_CHANGED_EVENT, handleProviderSettingsChanged);
    };
  }, [provider, selectedSession?.id, syncProviderSettingsFromStorage]);

  useEffect(() => {
    if (!selectedSession?.__provider || selectedSession.__provider === provider) {
      if (
        !selectedSession &&
        selectedDraftProvider &&
        selectedDraftSessionKey &&
        lastDraftSyncKeyRef.current !== selectedDraftSessionKey
      ) {
        lastDraftSyncKeyRef.current = selectedDraftSessionKey;
        setProviderState(selectedDraftProvider);
        if (typeof window !== 'undefined') {
          window.localStorage.setItem('selected-provider', selectedDraftProvider);
        }
      }
      return;
    }

    lastDraftSyncKeyRef.current = null;
    setProviderState(selectedSession.__provider);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('selected-provider', selectedSession.__provider);
    }
  }, [provider, selectedDraftProvider, selectedDraftSessionKey, selectedSession]);

  useEffect(() => {
    setPermissionMode(resolvePermissionMode(provider, selectedSession?.id));
  }, [provider, selectedSession?.id]);

  useEffect(() => {
    if (lastProviderRef.current === provider) {
      return;
    }

    setPendingPermissionRequests([]);
    lastProviderRef.current = provider;
  }, [provider]);

  useEffect(() => {
    setPendingPermissionRequests((previous) =>
      previous.filter((request) => !request.sessionId || request.sessionId === selectedSession?.id),
    );
  }, [selectedSession?.id]);

  const setProvider = useCallback((nextProvider: SessionProvider) => {
    setProviderState(nextProvider);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('selected-provider', nextProvider);
    }
  }, []);

  const setClaudeModel = useCallback((model: string) => {
    setClaudeModelState(model);
    saveStoredClaudeSettings({
      ...getStoredClaudeSettings(),
      model,
    });
  }, []);

  const setCodexModel = useCallback((model: string) => {
    setCodexModelState(model);
    saveStoredCodexSettings({
      ...getStoredCodexSettings(),
      model,
    });
  }, []);

  const setClaudeThinkingEffort = useCallback((thinkingEffort: ProviderThinkingEffort) => {
    setClaudeThinkingEffortState(thinkingEffort);
    saveStoredClaudeSettings({
      ...getStoredClaudeSettings(),
      thinkingEffort,
    });
  }, []);

  const setCodexReasoningEffort = useCallback((reasoningEffort: ProviderThinkingEffort) => {
    setCodexReasoningEffortState(reasoningEffort);
    saveStoredCodexSettings({
      ...getStoredCodexSettings(),
      reasoningEffort,
    });
  }, []);

  const cyclePermissionMode = useCallback(() => {
    const modes: PermissionMode[] =
      provider === 'codex'
        ? ['default', 'acceptEdits', 'bypassPermissions']
        : ['default', 'acceptEdits', 'bypassPermissions', 'plan'];

    const currentIndex = modes.indexOf(permissionMode);
    const nextIndex = (currentIndex + 1) % modes.length;
    const nextMode = modes[nextIndex];
    setPermissionMode(nextMode);

    if (selectedSession?.id && typeof window !== 'undefined') {
      window.localStorage.setItem(`permissionMode-${selectedSession.id}`, nextMode);
      return;
    }

    if (provider === 'codex') {
      saveStoredCodexSettings({
        ...getStoredCodexSettings(),
        permissionMode:
          nextMode === 'acceptEdits' || nextMode === 'bypassPermissions'
            ? nextMode
            : 'default',
      });
    }
  }, [permissionMode, provider, selectedSession?.id]);

  return {
    provider,
    setProvider,
    claudeModel: claudeModelState,
    setClaudeModel,
    claudeThinkingEffort: claudeThinkingEffortState,
    setClaudeThinkingEffort,
    codexModel: codexModelState,
    setCodexModel,
    codexReasoningEffort: codexReasoningEffortState,
    setCodexReasoningEffort,
    permissionMode,
    setPermissionMode,
    pendingPermissionRequests,
    setPendingPermissionRequests,
    cyclePermissionMode,
  };
}
