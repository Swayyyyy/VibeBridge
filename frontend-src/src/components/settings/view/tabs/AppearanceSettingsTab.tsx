import { useTranslation } from 'react-i18next';
import { DarkModeToggle } from '../../../../shared/view/ui';
import { useUiPreferences } from '../../../../hooks/useUiPreferences';
import {
  TOOL_DISPLAY_TOGGLES,
  VIEW_OPTION_TOGGLES,
  INPUT_SETTING_TOGGLES,
  WHISPER_OPTIONS,
} from '../../../quick-settings-panel/constants';
import { useWhisperMode } from '../../../quick-settings-panel/hooks/useWhisperMode';
import type { PreferenceToggleItem, PreferenceToggleKey } from '../../../quick-settings-panel/types';
import type { CodeEditorSettingsState, ProjectSortOrder } from '../../types/types';
import LanguageSelector from '../../../../shared/view/ui/LanguageSelector';
import SettingsCard from '../SettingsCard';
import SettingsRow from '../SettingsRow';
import SettingsSection from '../SettingsSection';
import SettingsToggle from '../SettingsToggle';

type AppearanceSettingsTabProps = {
  projectSortOrder: ProjectSortOrder;
  onProjectSortOrderChange: (value: ProjectSortOrder) => void;
  codeEditorSettings: CodeEditorSettingsState;
  onCodeEditorThemeChange: (value: 'dark' | 'light') => void;
  onCodeEditorWordWrapChange: (value: boolean) => void;
  onCodeEditorShowMinimapChange: (value: boolean) => void;
  onCodeEditorLineNumbersChange: (value: boolean) => void;
  onCodeEditorFontSizeChange: (value: string) => void;
};

export default function AppearanceSettingsTab({
  projectSortOrder,
  onProjectSortOrderChange,
  codeEditorSettings,
  onCodeEditorThemeChange,
  onCodeEditorWordWrapChange,
  onCodeEditorShowMinimapChange,
  onCodeEditorLineNumbersChange,
  onCodeEditorFontSizeChange,
}: AppearanceSettingsTabProps) {
  const { t } = useTranslation('settings');
  const { preferences, setPreference } = useUiPreferences();
  const { setWhisperMode, isOptionSelected } = useWhisperMode();

  const renderPreferenceRows = (items: PreferenceToggleItem[], descriptionKey?: string) => (
    items.map(({ key, labelKey }) => (
      <SettingsRow
        key={key}
        label={t(labelKey)}
        description={key === 'sendByCtrlEnter' && descriptionKey ? t(descriptionKey) : undefined}
      >
        <SettingsToggle
          checked={preferences[key as PreferenceToggleKey]}
          onChange={(value) => setPreference(key, value)}
          ariaLabel={t(labelKey)}
        />
      </SettingsRow>
    ))
  );

  return (
    <div className="space-y-8">
      <SettingsSection title={t('appearanceSettings.darkMode.label')}>
        <SettingsCard>
          <SettingsRow
            label={t('appearanceSettings.darkMode.label')}
            description={t('appearanceSettings.darkMode.description')}
          >
            <DarkModeToggle ariaLabel={t('appearanceSettings.darkMode.label')} />
          </SettingsRow>
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('mainTabs.appearance')}>
        <SettingsCard>
          <LanguageSelector />
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('appearanceSettings.projectSorting.label')}>
        <SettingsCard>
          <SettingsRow
            label={t('appearanceSettings.projectSorting.label')}
            description={t('appearanceSettings.projectSorting.description')}
          >
            <select
              value={projectSortOrder}
              onChange={(event) => onProjectSortOrderChange(event.target.value as ProjectSortOrder)}
              className="w-full rounded-lg border border-input bg-card p-2.5 text-sm text-foreground touch-manipulation focus:border-primary focus:ring-1 focus:ring-primary sm:w-36"
            >
              <option value="name">{t('appearanceSettings.projectSorting.alphabetical')}</option>
              <option value="date">{t('appearanceSettings.projectSorting.recentActivity')}</option>
            </select>
          </SettingsRow>
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('quickSettings.sections.toolDisplay')}>
        <SettingsCard divided>
          {renderPreferenceRows(TOOL_DISPLAY_TOGGLES)}
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('quickSettings.sections.viewOptions')}>
        <SettingsCard divided>
          {renderPreferenceRows(VIEW_OPTION_TOGGLES)}
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('quickSettings.sections.inputSettings')}>
        <SettingsCard divided>
          {renderPreferenceRows(INPUT_SETTING_TOGGLES, 'quickSettings.sendByCtrlEnterDescription')}
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('quickSettings.sections.whisperDictation')}>
        <SettingsCard className="space-y-2 p-2">
          {WHISPER_OPTIONS.map(({ value, titleKey, descriptionKey, icon: Icon }) => {
            const checked = isOptionSelected(value);

            return (
              <label
                key={value}
                className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-3 transition-colors ${
                  checked
                    ? 'border-primary bg-primary/5'
                    : 'border-border bg-card/30 hover:bg-accent/40'
                }`}
              >
                <input
                  type="radio"
                  name="whisperMode"
                  value={value}
                  checked={checked}
                  onChange={() => setWhisperMode(value)}
                  className="mt-0.5 h-4 w-4 border-border text-primary focus:ring-primary"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Icon className="h-4 w-4 text-muted-foreground" />
                    {t(titleKey)}
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{t(descriptionKey)}</p>
                </div>
              </label>
            );
          })}
        </SettingsCard>
      </SettingsSection>

      <SettingsSection title={t('appearanceSettings.codeEditor.title')}>
        <SettingsCard divided>
          <SettingsRow
            label={t('appearanceSettings.codeEditor.theme.label')}
            description={t('appearanceSettings.codeEditor.theme.description')}
          >
            <DarkModeToggle
              checked={codeEditorSettings.theme === 'dark'}
              onToggle={(enabled) => onCodeEditorThemeChange(enabled ? 'dark' : 'light')}
              ariaLabel={t('appearanceSettings.codeEditor.theme.label')}
            />
          </SettingsRow>

          <SettingsRow
            label={t('appearanceSettings.codeEditor.wordWrap.label')}
            description={t('appearanceSettings.codeEditor.wordWrap.description')}
          >
            <SettingsToggle
              checked={codeEditorSettings.wordWrap}
              onChange={onCodeEditorWordWrapChange}
              ariaLabel={t('appearanceSettings.codeEditor.wordWrap.label')}
            />
          </SettingsRow>

          <SettingsRow
            label={t('appearanceSettings.codeEditor.showMinimap.label')}
            description={t('appearanceSettings.codeEditor.showMinimap.description')}
          >
            <SettingsToggle
              checked={codeEditorSettings.showMinimap}
              onChange={onCodeEditorShowMinimapChange}
              ariaLabel={t('appearanceSettings.codeEditor.showMinimap.label')}
            />
          </SettingsRow>

          <SettingsRow
            label={t('appearanceSettings.codeEditor.lineNumbers.label')}
            description={t('appearanceSettings.codeEditor.lineNumbers.description')}
          >
            <SettingsToggle
              checked={codeEditorSettings.lineNumbers}
              onChange={onCodeEditorLineNumbersChange}
              ariaLabel={t('appearanceSettings.codeEditor.lineNumbers.label')}
            />
          </SettingsRow>

          <SettingsRow
            label={t('appearanceSettings.codeEditor.fontSize.label')}
            description={t('appearanceSettings.codeEditor.fontSize.description')}
          >
            <select
              value={codeEditorSettings.fontSize}
              onChange={(event) => onCodeEditorFontSizeChange(event.target.value)}
              className="w-full rounded-lg border border-input bg-card p-2.5 text-sm text-foreground touch-manipulation focus:border-primary focus:ring-1 focus:ring-primary sm:w-28"
            >
              <option value="10">10px</option>
              <option value="11">11px</option>
              <option value="12">12px</option>
              <option value="13">13px</option>
              <option value="14">14px</option>
              <option value="15">15px</option>
              <option value="16">16px</option>
              <option value="18">18px</option>
              <option value="20">20px</option>
            </select>
          </SettingsRow>
        </SettingsCard>
      </SettingsSection>
    </div>
  );
}
