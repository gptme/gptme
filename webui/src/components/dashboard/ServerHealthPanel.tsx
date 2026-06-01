import { type FC } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, Server, Loader2 } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import type { ServerHealth } from '@/types/api';

const REFRESH_INTERVAL_MS = 5000;

const HEALTH_CONFIG = {
  green: { label: 'Healthy', className: 'bg-emerald-500', textClass: 'text-emerald-600' },
  yellow: { label: 'Active', className: 'bg-amber-400', textClass: 'text-amber-600' },
  red: { label: 'Busy', className: 'bg-red-500', textClass: 'text-red-600' },
} satisfies Record<ServerHealth['health'], { label: string; className: string; textClass: string }>;

/** Compact slot strip: one colored pip per server session slot. */
const SlotStrip: FC<{ health: ServerHealth }> = ({ health }) => {
  if (health.slots.length === 0) {
    return <span className="text-xs text-muted-foreground">No active sessions</span>;
  }
  return (
    <div className="flex flex-wrap gap-1" title="Each pip represents one active session slot">
      {health.slots.map((slot) => (
        <div
          key={slot.id}
          className={`h-3 w-3 rounded-sm ${slot.generating ? 'animate-pulse bg-amber-400' : 'bg-emerald-400'}`}
          title={
            slot.generating ? `Generating (${Math.round(slot.elapsed_seconds ?? 0)}s)` : 'Idle'
          }
        />
      ))}
    </div>
  );
};

/**
 * Compact server connection health panel.
 *
 * Shows session count, generating/idle breakdown, a per-slot status strip,
 * and a color-coded health indicator. Designed to be embedded in admin views
 * or dashboard layouts.
 */
export const ServerHealthPanel: FC = () => {
  const { api } = useApi();
  const isConnected = use$(api.isConnected$);

  const {
    data: health,
    isLoading,
    error,
  } = useQuery<ServerHealth>({
    queryKey: ['server-health'],
    queryFn: () => api.getServerHealth(),
    enabled: isConnected,
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: 0,
  });

  if (!isConnected) {
    return (
      <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
        <Server className="h-4 w-4 shrink-0" />
        <span>Not connected</span>
      </div>
    );
  }

  if (isLoading && !health) {
    return (
      <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
        <span>Loading health…</span>
      </div>
    );
  }

  if (error || !health) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <Server className="h-4 w-4 shrink-0" />
        <span>Health endpoint unavailable</span>
      </div>
    );
  }

  const cfg = HEALTH_CONFIG[health.health];

  return (
    <div className="rounded-md border bg-card px-4 py-3">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-sm font-medium">Server Health</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${cfg.className}`} />
          <span className={`text-xs font-medium ${cfg.textClass}`}>{cfg.label}</span>
        </div>
      </div>

      {/* Stats row */}
      <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
        <span>
          <span className="font-semibold tabular-nums text-foreground">{health.session_count}</span>{' '}
          session{health.session_count !== 1 ? 's' : ''}
        </span>
        {health.generating_count > 0 && (
          <span className="flex items-center gap-1">
            <Activity className="h-3 w-3 text-amber-500" />
            <span className="font-semibold tabular-nums text-foreground">
              {health.generating_count}
            </span>{' '}
            generating
          </span>
        )}
        {health.idle_count > 0 && (
          <span>
            <span className="font-semibold tabular-nums text-foreground">{health.idle_count}</span>{' '}
            idle
          </span>
        )}
      </div>

      {/* Slot strip */}
      {health.slots.length > 0 && (
        <div className="mt-2">
          <SlotStrip health={health} />
        </div>
      )}
    </div>
  );
};
