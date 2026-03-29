import { useCallback, useEffect, useRef, useState } from 'react';
import { useTheme } from '../../../contexts/ThemeContext';
import {
  getStoredClaudeSettings,
  getStoredCodexSettings,
  saveStoredClaudeSettings,
  saveStoredCodexSettings,
} from '../../../shared/providerSettings';
import {
  DEFAULT_CODE_EDITOR_SETTINGS,
} from '../constants/constants';
import type {
  ClaudeAccountSettingsState,
  ClaudePermissionsState,
  CodeEditorSettingsState,
  CodexAccountSettingsState,
  CodexPermissionMode,
  ProjectSortOrder,
  SettingsMainTab,
  SettingsProject,
} from '../types/types';

type ThemeContextValue = {
  isDarkMode: boolean;
  toggleDarkMode: () => void;
};

type UseSettingsControllerArgs = {
  isOpen: boolean;
  initialTab: string;
  projects: SettingsProject[];
  onClose: () => void;
};

const KNOWN_MAIN_TABS: SettingsMainTab[] = ['account', 'agents', 'appearance', 'git'];

const normalizeMainTab = (tab: string): SettingsMainTab => {
  if (tab === 'tools' || tab === 'api' || tab === 'tasks' || tab === 'plugins') {
    return 'agents';
  }

  return KNOWN_MAIN_TABS.includes(tab as SettingsMainTab) ? (tab as SettingsMainTab) : 'agents';
};

const toCodexPermissionMode = (value: unknown): CodexPermissionMode => {
  if (value === 'acceptEdits' || value === 'bypassPermissions') {
    return value;
  }

  return 'default';
};

const readCodeEditorSettings = (): CodeEditorSettingsState => ({
  theme: localStorage.getItem('codeEditorTheme') === 'light' ? 'light' : 'dark',
  wordWrap: localStorage.getItem('codeEditorWordWrap') === 'true',
  showMinimap: localStorage.getItem('codeEditorShowMinimap') !== 'false',
  lineNumbers: localStorage.getItem('codeEditorLineNumbers') !== 'false',
  fontSize: localStorage.getItem('codeEditorFontSize') ?? DEFAULT_CODE_EDITOR_SETTINGS.fontSize,
});

const createEmptyClaudePermissions = (): ClaudePermissionsState => ({
  allowedTools: [],
  disallowedTools: [],
  skipPermissions: false,
});

const createDefaultClaudeAccountSettings = (): ClaudeAccountSettingsState => {
  const saved = getStoredClaudeSettings();
  return {
    command: saved.command,
    model: saved.model,
    thinkingEffort: saved.thinkingEffort,
  };
};

const createDefaultCodexAccountSettings = (): CodexAccountSettingsState => {
  const saved = getStoredCodexSettings();
  return {
    command: saved.command,
    model: saved.model,
    reasoningEffort: saved.reasoningEffort,
  };
};

export function useSettingsController({ isOpen, initialTab }: UseSettingsControllerArgs) {
  const { isDarkMode, toggleDarkMode } = useTheme() as ThemeContextValue;
  const closeTimerRef = useRef<number | null>(null);

  const [activeTab, setActiveTab] = useState<SettingsMainTab>(() => normalizeMainTab(initialTab));
  const [saveStatus, setSaveStatus] = useState<'success' | 'error' | null>(null);
  const [projectSortOrder, setProjectSortOrder] = useState<ProjectSortOrder>('name');
  const [codeEditorSettings, setCodeEditorSettings] = useState<CodeEditorSettingsState>(() => (
    readCodeEditorSettings()
  ));

  const [claudePermissions, setClaudePermissions] = useState<ClaudePermissionsState>(() => (
    createEmptyClaudePermissions()
  ));
  const [claudeAccountSettings, setClaudeAccountSettings] = useState<ClaudeAccountSettingsState>(() => (
    createDefaultClaudeAccountSettings()
  ));
  const [codexAccountSettings, setCodexAccountSettings] = useState<CodexAccountSettingsState>(() => (
    createDefaultCodexAccountSettings()
  ));
  const [codexPermissionMode, setCodexPermissionMode] = useState<CodexPermissionMode>('default');

  const loadSettings = useCallback(async () => {
    try {
      const savedClaudeSettings = getStoredClaudeSettings();
      setClaudePermissions({
        allowedTools: savedClaudeSettings.allowedTools || [],
        disallowedTools: savedClaudeSettings.disallowedTools || [],
        skipPermissions: Boolean(savedClaudeSettings.skipPermissions),
      });
      setProjectSortOrder(savedClaudeSettings.projectSortOrder === 'date' ? 'date' : 'name');
      setClaudeAccountSettings({
        command: savedClaudeSettings.command,
        model: savedClaudeSettings.model,
        thinkingEffort: savedClaudeSettings.thinkingEffort,
      });

      const savedCodexSettings = getStoredCodexSettings();
      setCodexPermissionMode(toCodexPermissionMode(savedCodexSettings.permissionMode));
      setCodexAccountSettings({
        command: savedCodexSettings.command,
        model: savedCodexSettings.model,
        reasoningEffort: savedCodexSettings.reasoningEffort,
      });
    } catch (error) {
      console.error('Error loading settings:', error);
      setClaudePermissions(createEmptyClaudePermissions());
      setClaudeAccountSettings(createDefaultClaudeAccountSettings());
      setCodexAccountSettings(createDefaultCodexAccountSettings());
      setCodexPermissionMode('default');
      setProjectSortOrder('name');
    }
  }, []);

  const saveSettings = useCallback(() => {
    try {
      const now = new Date().toISOString();
      saveStoredClaudeSettings({
        allowedTools: claudePermissions.allowedTools,
        disallowedTools: claudePermissions.disallowedTools,
        skipPermissions: claudePermissions.skipPermissions,
        projectSortOrder,
        command: claudeAccountSettings.command.trim(),
        model: claudeAccountSettings.model,
        thinkingEffort: claudeAccountSettings.thinkingEffort,
        lastUpdated: now,
      });

      saveStoredCodexSettings({
        permissionMode: codexPermissionMode,
        command: codexAccountSettings.command.trim(),
        model: codexAccountSettings.model,
        reasoningEffort: codexAccountSettings.reasoningEffort,
        lastUpdated: now,
      });

      setSaveStatus('success');
    } catch (error) {
      console.error('Error saving settings:', error);
      setSaveStatus('error');
    }
  }, [
    claudePermissions.allowedTools,
    claudePermissions.disallowedTools,
    claudePermissions.skipPermissions,
    claudeAccountSettings.command,
    claudeAccountSettings.model,
    claudeAccountSettings.thinkingEffort,
    codexAccountSettings.command,
    codexAccountSettings.model,
    codexAccountSettings.reasoningEffort,
    codexPermissionMode,
    projectSortOrder,
  ]);

  const updateCodeEditorSetting = useCallback(
    <K extends keyof CodeEditorSettingsState>(key: K, value: CodeEditorSettingsState[K]) => {
      setCodeEditorSettings((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    setActiveTab(normalizeMainTab(initialTab));
    void loadSettings();
  }, [initialTab, isOpen, loadSettings]);

  useEffect(() => {
    localStorage.setItem('codeEditorTheme', codeEditorSettings.theme);
    localStorage.setItem('codeEditorWordWrap', String(codeEditorSettings.wordWrap));
    localStorage.setItem('codeEditorShowMinimap', String(codeEditorSettings.showMinimap));
    localStorage.setItem('codeEditorLineNumbers', String(codeEditorSettings.lineNumbers));
    localStorage.setItem('codeEditorFontSize', codeEditorSettings.fontSize);
    window.dispatchEvent(new Event('codeEditorSettingsChanged'));
  }, [codeEditorSettings]);

  const autoSaveTimerRef = useRef<number | null>(null);
  const isInitialLoadRef = useRef(true);

  useEffect(() => {
    if (isInitialLoadRef.current) {
      isInitialLoadRef.current = false;
      return;
    }

    if (autoSaveTimerRef.current !== null) {
      window.clearTimeout(autoSaveTimerRef.current);
    }

    autoSaveTimerRef.current = window.setTimeout(() => {
      saveSettings();
    }, 500);

    return () => {
      if (autoSaveTimerRef.current !== null) {
        window.clearTimeout(autoSaveTimerRef.current);
      }
    };
  }, [saveSettings]);

  useEffect(() => {
    if (saveStatus === null) {
      return;
    }

    const timer = window.setTimeout(() => setSaveStatus(null), 2000);
    return () => window.clearTimeout(timer);
  }, [saveStatus]);

  useEffect(() => {
    if (isOpen) {
      isInitialLoadRef.current = true;
    }
  }, [isOpen]);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    if (autoSaveTimerRef.current !== null) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
  }, []);

  return {
    activeTab,
    setActiveTab,
    isDarkMode,
    toggleDarkMode,
    saveStatus,
    projectSortOrder,
    setProjectSortOrder,
    codeEditorSettings,
    updateCodeEditorSetting,
    claudePermissions,
    setClaudePermissions,
    claudeAccountSettings,
    setClaudeAccountSettings,
    codexAccountSettings,
    setCodexAccountSettings,
    codexPermissionMode,
    setCodexPermissionMode,
  };
}
