import { useTranslation } from 'react-i18next';

type TokenUsagePieProps = {
  used: number;
  total: number;
};

function formatPercent(value: number, language: string): string {
  const normalized = Math.max(0, Math.min(100, value));
  const rounded = Math.round(normalized);
  return `${new Intl.NumberFormat(language, { maximumFractionDigits: 0 }).format(rounded)}%`;
}

function formatCompactTokenCount(value: number, language: string): string {
  const absValue = Math.abs(value);

  if (absValue < 1_000) {
    return new Intl.NumberFormat(language, { maximumFractionDigits: 0 }).format(value);
  }

  const units = [
    { threshold: 1_000_000_000, suffix: 'b' },
    { threshold: 1_000_000, suffix: 'm' },
    { threshold: 1_000, suffix: 'k' },
  ];

  const unit = units.find(({ threshold }) => absValue >= threshold);
  if (!unit) {
    return new Intl.NumberFormat(language, { maximumFractionDigits: 0 }).format(value);
  }

  const scaled = value / unit.threshold;
  const fractionDigits = Math.abs(scaled) < 10 ? 1 : 0;
  const formatted = new Intl.NumberFormat(language, {
    maximumFractionDigits: fractionDigits,
    minimumFractionDigits: 0,
  }).format(scaled);

  return `${formatted}${unit.suffix}`;
}

export default function TokenUsagePie({ used, total }: TokenUsagePieProps) {
  const { t, i18n } = useTranslation('chat');
  // Token usage visualization component
  // Only bail out on missing values or non‐positive totals; allow used===0 to render 0%
  if (used == null || total == null || total <= 0) return null;

  const percentage = Math.max(0, Math.min(100, (used / total) * 100));
  const usedPercentLabel = formatPercent(percentage, i18n.language);
  const remainingPercentLabel = formatPercent(100 - percentage, i18n.language);
  const usedTokensLabel = formatCompactTokenCount(used, i18n.language);
  const totalTokensLabel = formatCompactTokenCount(total, i18n.language);
  const tooltipLines = [
    t('tokenUsage.tooltip.title', { defaultValue: 'Context window' }),
    t('tokenUsage.tooltip.percentageSummary', {
      defaultValue: '{{used}} used ({{remaining}} remaining)',
      used: usedPercentLabel,
      remaining: remainingPercentLabel,
    }),
    t('tokenUsage.tooltip.tokensSummary', {
      defaultValue: '{{usedTokens}} tokens used, {{totalTokens}} total',
      usedTokens: usedTokensLabel,
      totalTokens: totalTokensLabel,
    }),
  ];
  const tooltipTitle = tooltipLines.join('\n');
  const radius = 10;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percentage / 100) * circumference;

  // Color based on usage level
  const getColor = () => {
    if (percentage < 50) return '#3b82f6'; // blue
    if (percentage < 75) return '#f59e0b'; // orange
    return '#ef4444'; // red
  };

  return (
    <div className="group relative">
      <button
        type="button"
        className="flex items-center gap-2 rounded-md px-1 py-0.5 text-xs text-gray-600 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 dark:text-gray-400"
        aria-label={t('tokenUsage.ariaLabel', { defaultValue: 'Context window usage' })}
        title={tooltipTitle}
      >
        <svg width="24" height="24" viewBox="0 0 24 24" className="-rotate-90 transform">
          {/* Background circle */}
          <circle
            cx="12"
            cy="12"
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="text-gray-300 dark:text-gray-600"
          />
          {/* Progress circle */}
          <circle
            cx="12"
            cy="12"
            r={radius}
            fill="none"
            stroke={getColor()}
            strokeWidth="2"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
          />
        </svg>
        <span>{usedPercentLabel}</span>
      </button>

      <div className="pointer-events-none invisible absolute bottom-full left-1/2 z-50 mb-3 w-max min-w-[196px] -translate-x-1/2 rounded-xl border border-border/70 bg-popover px-3 py-2 text-left text-xs leading-[18px] text-popover-foreground opacity-0 shadow-xl transition-all duration-150 ease-out group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
        <div className="text-[10px] font-medium tracking-[0.06em] text-muted-foreground">
          {t('tokenUsage.tooltip.title', { defaultValue: 'Context window' })}
        </div>
        <div className="mt-1 text-sm font-semibold leading-5 text-foreground">
          {t('tokenUsage.tooltip.percentageSummary', {
            defaultValue: '{{used}} used ({{remaining}} remaining)',
            used: usedPercentLabel,
            remaining: remainingPercentLabel,
          })}
        </div>
        <div className="mt-0.5 text-sm font-semibold leading-5 text-foreground">
          {t('tokenUsage.tooltip.tokensSummary', {
            defaultValue: '{{usedTokens}} tokens used, {{totalTokens}} total',
            usedTokens: usedTokensLabel,
            totalTokens: totalTokensLabel,
          })}
        </div>
        <div className="absolute left-1/2 top-full h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 border-b border-r border-border/70 bg-popover" />
      </div>
    </div>
  );
}
