import { PillBar, Pill } from '../../../../../../shared/view/ui';
import SessionProviderLogo from '../../../../../llm-logo-provider/SessionProviderLogo';
import type { AgentProvider } from '../../../../types/types';
import type { AgentSelectorSectionProps } from '../types';

const AGENT_PROVIDERS: AgentProvider[] = ['claude', 'codex'];

const AGENT_NAMES: Record<AgentProvider, string> = {
  claude: 'Claude',
  codex: 'Codex',
};

export default function AgentSelectorSection({
  selectedAgent,
  onSelectAgent,
}: AgentSelectorSectionProps) {
  return (
    <div className="flex-shrink-0 border-b border-border px-3 py-2 md:px-4 md:py-3">
      <PillBar className="w-full md:w-auto">
        {AGENT_PROVIDERS.map((agent) => (
          <Pill
            key={agent}
            isActive={selectedAgent === agent}
            onClick={() => onSelectAgent(agent)}
            className="min-w-0 flex-1 justify-center md:flex-initial"
          >
            <SessionProviderLogo provider={agent} className="h-4 w-4 flex-shrink-0" />
            <span className="truncate">{AGENT_NAMES[agent]}</span>
          </Pill>
        ))}
      </PillBar>
    </div>
  );
}
