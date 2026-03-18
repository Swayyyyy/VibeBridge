import React from "react";
import { Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import SessionProviderLogo from "../../../llm-logo-provider/SessionProviderLogo";
import type { ProviderThinkingEffort } from "../../../../shared/providerSettings";
import {
  CLAUDE_MODELS,
  CODEX_MODELS,
} from "../../../../../shared/modelConstants";
import type { ProjectSession, SessionProvider } from "../../../../types/app";
import { NextTaskBanner } from "../../../task-master";

type ProviderSelectionEmptyStateProps = {
  selectedSession: ProjectSession | null;
  currentSessionId: string | null;
  provider: SessionProvider;
  setProvider: (next: SessionProvider) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  claudeModel: string;
  setClaudeModel: (model: string) => void;
  claudeThinkingEffort: ProviderThinkingEffort;
  setClaudeThinkingEffort: (effort: ProviderThinkingEffort) => void;
  codexModel: string;
  setCodexModel: (model: string) => void;
  codexReasoningEffort: ProviderThinkingEffort;
  setCodexReasoningEffort: (effort: ProviderThinkingEffort) => void;
  tasksEnabled: boolean;
  isTaskMasterInstalled: boolean | null;
  onShowAllTasks?: (() => void) | null;
  setInput: React.Dispatch<React.SetStateAction<string>>;
};

type ProviderDef = {
  id: SessionProvider;
  name: string;
  infoKey: string;
  accent: string;
  ring: string;
  check: string;
};

const PROVIDERS: ProviderDef[] = [
  {
    id: "claude",
    name: "Claude Code",
    infoKey: "providerSelection.providerInfo.anthropic",
    accent: "border-primary",
    ring: "ring-primary/15",
    check: "bg-primary text-primary-foreground",
  },
  {
    id: "codex",
    name: "Codex",
    infoKey: "providerSelection.providerInfo.openai",
    accent: "border-emerald-600 dark:border-emerald-400",
    ring: "ring-emerald-600/15",
    check: "bg-emerald-600 dark:bg-emerald-500 text-white",
  },
];

function getModelConfig(p: SessionProvider) {
  if (p === "claude") return CLAUDE_MODELS;
  if (p === "codex") return CODEX_MODELS;
  return CLAUDE_MODELS;
}

function getModelValue(
  p: SessionProvider,
  c: string,
  co: string,
) {
  if (p === "claude") return c;
  return co;
}

const THINKING_EFFORT_VALUES: ProviderThinkingEffort[] = ["low", "medium", "high", "ultra-high"];

export default function ProviderSelectionEmptyState({
  selectedSession,
  currentSessionId,
  provider,
  setProvider,
  textareaRef,
  claudeModel,
  setClaudeModel,
  claudeThinkingEffort,
  setClaudeThinkingEffort,
  codexModel,
  setCodexModel,
  codexReasoningEffort,
  setCodexReasoningEffort,
  tasksEnabled,
  isTaskMasterInstalled,
  onShowAllTasks,
  setInput,
}: ProviderSelectionEmptyStateProps) {
  const { t } = useTranslation("chat");
  const nextTaskPrompt = t("tasks.nextTaskPrompt", {
    defaultValue: "Start the next task",
  });
  const effortLabels: Record<ProviderThinkingEffort, string> = {
    low: t("thinkingMode.simple.low", { defaultValue: "Low" }),
    medium: t("thinkingMode.simple.medium", { defaultValue: "Medium" }),
    high: t("thinkingMode.simple.high", { defaultValue: "High" }),
    "ultra-high": t("thinkingMode.simple.ultraHigh", { defaultValue: "Ultra-high" }),
  };

  const selectProvider = (next: SessionProvider) => {
    setProvider(next);
    setTimeout(() => textareaRef.current?.focus(), 100);
  };

  const handleModelChange = (value: string) => {
    if (provider === "claude") {
      setClaudeModel(value);
    } else {
      setCodexModel(value);
    }
  };

  const currentEffort = provider === "claude" ? claudeThinkingEffort : codexReasoningEffort;

  const handleEffortChange = (value: ProviderThinkingEffort) => {
    if (provider === "claude") {
      setClaudeThinkingEffort(value);
      return;
    }

    setCodexReasoningEffort(value);
  };

  const modelConfig = getModelConfig(provider);
  const currentModel = getModelValue(
    provider,
    claudeModel,
    codexModel,
  );

  /* ── New session — provider picker ── */
  if (!selectedSession && !currentSessionId) {
    return (
      <div className="flex h-full items-center justify-center px-4">
        <div className="w-full max-w-md">
          {/* Heading */}
          <div className="mb-8 text-center">
            <h2 className="text-lg font-semibold tracking-tight text-foreground sm:text-xl">
              {t("providerSelection.title")}
            </h2>
            <p className="mt-1 text-[13px] text-muted-foreground">
              {t("providerSelection.description")}
            </p>
          </div>

          {/* Provider cards — horizontal row, equal width */}
          <div className="mb-6 grid grid-cols-2 gap-2 sm:gap-2.5">
            {PROVIDERS.map((p) => {
              const active = provider === p.id;
              return (
                <button
                  key={p.id}
                  onClick={() => selectProvider(p.id)}
                  className={`
                    relative flex flex-col items-center gap-2.5 rounded-xl border-[1.5px] px-2
                    pb-4 pt-5 transition-all duration-150
                    active:scale-[0.97]
                    ${
                      active
                        ? `${p.accent} ${p.ring} bg-card shadow-sm ring-2`
                        : "border-border bg-card/60 hover:border-border/80 hover:bg-card"
                    }
                  `}
                >
                  <SessionProviderLogo
                    provider={p.id}
                    className={`h-9 w-9 transition-transform duration-150 ${active ? "scale-110" : ""}`}
                  />
                  <div className="text-center">
                    <p className="text-[13px] font-semibold leading-none text-foreground">
                      {p.name}
                    </p>
                    <p className="mt-1 text-[10px] leading-tight text-muted-foreground">
                      {t(p.infoKey)}
                    </p>
                  </div>
                  {/* Check badge */}
                  {active && (
                    <div
                      className={`absolute -right-1 -top-1 h-[18px] w-[18px] rounded-full ${p.check} flex items-center justify-center shadow-sm`}
                    >
                      <Check className="h-2.5 w-2.5" strokeWidth={3} />
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          {/* Model picker — appears after provider is chosen */}
          <div
            className={`transition-all duration-200 ${provider ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-1 opacity-0"}`}
          >
            <div className="mb-5 flex flex-wrap items-center justify-center gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">
                  {t("providerSelection.selectModel")}
                </span>
                <div className="relative">
                  <select
                    value={currentModel}
                    onChange={(e) => handleModelChange(e.target.value)}
                    tabIndex={-1}
                    className="cursor-pointer appearance-none rounded-lg border border-border/60 bg-muted/50 py-1.5 pl-3 pr-7 text-sm font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-primary/20"
                  >
                    {modelConfig.OPTIONS.map(
                      ({ value, label }: { value: string; label: string }) => (
                        <option key={value + label} value={value}>
                          {label}
                        </option>
                      ),
                    )}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">
                  {t("providerSelection.selectStrength", { defaultValue: "Strength" })}
                </span>
                <div className="relative">
                  <select
                    value={currentEffort}
                    onChange={(e) => handleEffortChange(e.target.value as ProviderThinkingEffort)}
                    tabIndex={-1}
                    className="cursor-pointer appearance-none rounded-lg border border-border/60 bg-muted/50 py-1.5 pl-3 pr-7 text-sm font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-primary/20"
                  >
                    {THINKING_EFFORT_VALUES.map((value) => (
                      <option key={value} value={value}>
                        {effortLabels[value]}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
                </div>
              </div>
            </div>

            <p className="text-center text-sm text-muted-foreground/70">
              {
                {
                  claude: t("providerSelection.readyPrompt.claude", {
                    model: claudeModel,
                  }),
                  codex: t("providerSelection.readyPrompt.codex", {
                    model: codexModel,
                  }),
                }[provider]
              }
            </p>
          </div>

          {/* Task banner */}
          {provider && tasksEnabled && isTaskMasterInstalled && (
            <div className="mt-5">
              <NextTaskBanner
                onStartTask={() => setInput(nextTaskPrompt)}
                onShowAllTasks={onShowAllTasks}
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  /* ── Existing session — continue prompt ── */
  if (selectedSession) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md px-6 text-center">
          <p className="mb-1.5 text-lg font-semibold text-foreground">
            {t("session.continue.title")}
          </p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t("session.continue.description")}
          </p>

          {tasksEnabled && isTaskMasterInstalled && (
            <div className="mt-5">
              <NextTaskBanner
                onStartTask={() => setInput(nextTaskPrompt)}
                onShowAllTasks={onShowAllTasks}
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  return null;
}
