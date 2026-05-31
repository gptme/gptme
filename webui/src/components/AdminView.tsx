import { type FC, useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, RefreshCw, Trash2, AlertCircle, Loader2, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import type { ActiveSession } from '@/types/api';
import { getRelativeTimeString } from '@/utils/time';

// Auto-refresh interval (ms)
const REFRESH_INTERVAL_MS = 5000;

function formatElapsed(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

interface SessionRowProps {
  session: ActiveSession;
  onKill: (id: string) => void;
  isKilling: boolean;
}

const SessionRow: FC<SessionRowProps> = ({ session, onKill, isKilling }) => {
  const lastActivity = session.last_activity
    ? getRelativeTimeString(new Date(session.last_activity))
    : '—';

  const handleKill = () => {
    if (
      window.confirm(
        `Kill session ${session.id.slice(0, 8)}…?\nThis will interrupt any running generation.`
      )
    ) {
      onKill(session.id);
    }
  };

  return (
    <tr className="border-b transition-colors last:border-b-0 hover:bg-muted/30">
      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
        {session.id.slice(0, 8)}
        <span className="text-muted-foreground/50">…</span>
      </td>
      <td className="px-4 py-3 text-sm">
        {session.conversation_id ? (
          <span className="font-mono text-xs">{session.conversation_id}</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-sm">
        {session.model ? (
          <span className="font-mono text-xs">{session.model}</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-right text-sm tabular-nums">{session.message_count}</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1.5">
          {session.generating ? (
            <>
              <Badge variant="default" className="h-5 gap-1 px-1.5 text-[10px]">
                <Activity className="h-2.5 w-2.5" />
                generating
              </Badge>
              <span className="text-xs tabular-nums text-muted-foreground">
                {formatElapsed(session.elapsed_seconds)}
              </span>
            </>
          ) : (
            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
              idle
            </Badge>
          )}
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-muted-foreground">{lastActivity}</td>
      <td className="px-4 py-3">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={handleKill}
          disabled={isKilling}
          aria-label={`Kill session ${session.id.slice(0, 8)}`}
        >
          {isKilling ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
        </Button>
      </td>
    </tr>
  );
};

export const AdminView: FC = () => {
  const { api } = useApi();
  const isConnected = use$(api.isConnected$);
  const queryClient = useQueryClient();
  const [killingIds, setKillingIds] = useState<Set<string>>(new Set());

  const {
    data: sessions = [],
    isLoading,
    error,
    dataUpdatedAt,
    refetch,
  } = useQuery<ActiveSession[]>({
    queryKey: ['admin-sessions'],
    queryFn: () => api.getSessions(),
    enabled: isConnected,
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: 0,
  });

  const killMutation = useMutation({
    mutationFn: (sessionId: string) => api.deleteSession(sessionId),
    onMutate: (sessionId) => {
      setKillingIds((prev) => new Set(prev).add(sessionId));
    },
    onSettled: (_, __, sessionId) => {
      setKillingIds((prev) => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: ['admin-sessions'] });
    },
  });

  const handleKill = useCallback(
    (sessionId: string) => {
      killMutation.mutate(sessionId);
    },
    [killMutation]
  );

  const lastRefresh = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : null;

  const activeSessions = sessions.filter((s) => s.generating);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-muted-foreground" />
            <div>
              <h1 className="text-base font-semibold">Admin Panel</h1>
              <p className="text-xs text-muted-foreground">
                Active server sessions — auto-refreshes every 5s
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <span className="text-xs text-muted-foreground">Updated {lastRefresh}</span>
            )}
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5"
              onClick={() => refetch()}
              disabled={isLoading}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </div>

        {/* Summary stats */}
        <div className="mt-3 flex gap-4">
          <div className="rounded-md border bg-card px-3 py-1.5">
            <p className="text-xs text-muted-foreground">Total sessions</p>
            <p className="text-lg font-semibold tabular-nums">{sessions.length}</p>
          </div>
          <div className="rounded-md border bg-card px-3 py-1.5">
            <p className="text-xs text-muted-foreground">Generating</p>
            <p className="text-lg font-semibold tabular-nums text-primary">
              {activeSessions.length}
            </p>
          </div>
          <div className="rounded-md border bg-card px-3 py-1.5">
            <p className="text-xs text-muted-foreground">Idle</p>
            <p className="text-lg font-semibold tabular-nums text-muted-foreground">
              {sessions.length - activeSessions.length}
            </p>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {error ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
            <AlertCircle className="h-10 w-10 text-destructive" />
            <div>
              <p className="font-medium">Failed to load sessions</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {error instanceof Error ? error.message : 'An unexpected error occurred.'}
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        ) : isLoading && sessions.length === 0 ? (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Loading sessions…</span>
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-muted-foreground">
            <Shield className="h-10 w-10" />
            <p className="text-sm font-medium">No active sessions</p>
            <p className="text-xs">Sessions appear here when users open conversations.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 border-b bg-background">
              <tr className="text-left text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">Session ID</th>
                <th className="px-4 py-2 font-medium">Conversation</th>
                <th className="px-4 py-2 font-medium">Model</th>
                <th className="px-4 py-2 text-right font-medium">Messages</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Last activity</th>
                <th className="sr-only px-4 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  onKill={handleKill}
                  isKilling={killingIds.has(session.id)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
