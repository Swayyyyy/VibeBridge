import { X } from 'lucide-react';
import StandaloneShell from '../../standalone-shell/view/StandaloneShell';
import { IS_PLATFORM } from '../../../constants/config';
import { getDefaultProviderLoginCommand } from '../../../shared/providerSettings';
import type { CliProvider } from '../types';

type LoginModalProject = {
  name?: string;
  displayName?: string;
  fullPath?: string;
  path?: string;
  [key: string]: unknown;
};

type ProviderLoginModalProps = {
  isOpen: boolean;
  onClose: () => void;
  provider?: CliProvider;
  project?: LoginModalProject | null;
  onComplete?: (exitCode: number) => void;
  customCommand?: string;
  isAuthenticated?: boolean;
  isOnboarding?: boolean;
};

const getProviderCommand = ({
  provider,
  customCommand,
  isAuthenticated,
  isOnboarding,
}: {
  provider: CliProvider;
  customCommand?: string;
  isAuthenticated: boolean;
  isOnboarding: boolean;
}) => {
  if (customCommand) {
    return customCommand;
  }

  return getDefaultProviderLoginCommand({
    provider,
    isAuthenticated,
    isOnboarding,
  });
};

const getProviderTitle = (provider: CliProvider) => {
  if (provider === 'claude') return 'Claude CLI Login';
  return 'Codex CLI Login';
};

const normalizeProject = (project?: LoginModalProject | null) => {
  const normalizedName = project?.name || 'default';
  const normalizedFullPath = project?.fullPath ?? project?.path ?? (IS_PLATFORM ? '/workspace' : '');

  return {
    name: normalizedName,
    displayName: project?.displayName || normalizedName,
    fullPath: normalizedFullPath,
    path: project?.path ?? normalizedFullPath,
  };
};

export default function ProviderLoginModal({
  isOpen,
  onClose,
  provider = 'claude',
  project = null,
  onComplete,
  customCommand,
  isAuthenticated = false,
  isOnboarding = false,
}: ProviderLoginModalProps) {
  if (!isOpen) {
    return null;
  }

  const command = getProviderCommand({ provider, customCommand, isAuthenticated, isOnboarding });
  const title = getProviderTitle(provider);
  const shellProject = normalizeProject(project);

  const handleComplete = (exitCode: number) => {
    onComplete?.(exitCode);
    // Keep the modal open so users can read terminal output before closing.
  };

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black bg-opacity-50 max-md:items-stretch max-md:justify-stretch">
      <div className="flex h-3/4 w-full max-w-4xl flex-col rounded-lg bg-white shadow-xl dark:bg-gray-800 max-md:m-0 max-md:h-full max-md:max-w-none max-md:rounded-none md:m-4 md:h-3/4 md:max-w-4xl md:rounded-lg">
        <div className="flex items-center justify-between border-b border-gray-200 p-4 dark:border-gray-700">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{title}</h3>
          <button
            onClick={onClose}
            className="text-gray-400 transition-colors hover:text-gray-600 dark:hover:text-gray-300"
            aria-label="Close login modal"
          >
            <X className="h-6 w-6" />
          </button>
        </div>

        <div className="flex-1 overflow-hidden">
          <StandaloneShell project={shellProject} command={command} onComplete={handleComplete} minimal={true} />
        </div>
      </div>
    </div>
  );
}
