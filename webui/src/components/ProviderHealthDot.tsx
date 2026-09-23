import { use$ } from '@legendapp/state/react';
import type { FC } from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { providerHealth$ } from '@/stores/providerHealth';
import type { ProviderHealthStatus } from '@/stores/providerHealth';

const DOT_CLASS: Record<ProviderHealthStatus, string> = {
  ok: 'bg-emerald-500',
  configured: 'bg-sky-500',
  error: 'bg-red-500',
};

const STATUS_LABEL: Record<ProviderHealthStatus, string> = {
  ok: 'Reachable',
  configured: 'Configured (not yet checked)',
  error: 'Unreachable',
};

/**
 * A small colour-coded dot showing the server-side health of a single
 * provider. Reads from the shared providerHealth$ store — no extra fetches.
 * Returns null when the provider isn't in the health data yet.
 */
export const ProviderHealthDot: FC<{ provider: string }> = ({ provider }) => {
  const data = use$(providerHealth$.data);
  const entry = data?.providers?.[provider];

  if (!entry) return null;

  const dotClass = DOT_CLASS[entry.status] ?? 'bg-gray-400';
  const label = STATUS_LABEL[entry.status] ?? entry.status;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={`inline-block h-2 w-2 rounded-full ${dotClass} cursor-default`}
            aria-label={`${provider} health: ${label}`}
          />
        </TooltipTrigger>
        <TooltipContent>
          <p className="font-medium">
            {provider}: {label}
          </p>
          {entry.error && <p className="text-xs text-muted-foreground">{entry.error}</p>}
          {entry.latency_ms !== null && (
            <p className="text-xs text-muted-foreground">{entry.latency_ms} ms</p>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};
