import { type FC, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, Clock, Cpu, FolderOpen, AlertCircle, Search, ChevronRight, X } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import { getRelativeTimeString } from '@/utils/time';
import type { ExternalSessionCatalogItem } from '@/types/api';
import { ApiClientError } from '@/utils/api';

const HARNESS_LABELS: Record<string, string> = {
  'claude-code': 'Claude Code',
  gptme: 'gptme',
  codex: 'Codex',
  copilot: 'Copilot',
};

const HarnessIcon: FC<{ className?: string }> = ({ className }) => {
  return <Bot className={className} />;
};

interface SessionCardProps {
  session: ExternalSessionCatalogItem;
  onClick: () => void;
  selected: boolean;
}

const SessionCard: FC<SessionCardProps> = ({ session, onClick, selected }) => {
  const displayName = session.session_name ?? session.session_id.slice(0, 8);
  const harness = HARNESS_LABELS[session.harness] ?? session.harness;
  const timeStr = session.last_activity
    ? getRelativeTimeString(new Date(session.last_activity))
    : session.started_at
      ? getRelativeTimeString(new Date(session.started_at))
      : null;

  return (
    <button
      className={`w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 ${
        selected ? 'border-primary bg-muted/30' : 'bg-card'
      }`}
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <HarnessIcon className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
            <span className="truncate text-sm font-medium">{displayName}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span className="flex items-center gap-0.5">
              <Bot className="h-3 w-3" />
              {harness}
            </span>
            {session.model && (
              <span className="flex items-center gap-0.5">
                <Cpu className="h-3 w-3" />
                {session.model}
              </span>
            )}
            {session.project && (
              <span className="flex items-center gap-0.5">
                <FolderOpen className="h-3 w-3" />
                <span className="max-w-[120px] truncate">{session.project}</span>
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-shrink-0 flex-col items-end gap-1">
          {timeStr && (
            <span className="flex items-center gap-0.5 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              {timeStr}
            </span>
          )}
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        </div>
      </div>
      {session.capabilities.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {session.capabilities.slice(0, 3).map((cap) => (
            <Badge key={cap} variant="secondary" className="h-4 px-1.5 text-[10px]">
              {cap}
            </Badge>
          ))}
          {session.capabilities.length > 3 && (
            <Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
              +{session.capabilities.length - 3}
            </Badge>
          )}
        </div>
      )}
    </button>
  );
};

interface SessionDetailProps {
  sessionId: string;
  onClose: () => void;
}

const SessionDetail: FC<SessionDetailProps> = ({ sessionId, onClose }) => {
  const { api } = useApi();
  const isConnected = use$(api.isConnected$);

  const { data, isLoading, error } = useQuery({
    queryKey: ['external-session-detail', sessionId],
    queryFn: () => api.getExternalSession(sessionId),
    enabled: isConnected && !!sessionId,
  });

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        Loading session…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <AlertCircle className="h-8 w-8" />
        <p className="text-sm">Failed to load session details</p>
      </div>
    );
  }

  const transcript = data.transcript as Record<string, unknown>;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <h2 className="text-sm font-medium">Session details</h2>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
          {JSON.stringify(transcript, null, 2)}
        </pre>
      </div>
    </div>
  );
};

export const ExternalSessionsView: FC = () => {
  const { api } = useApi();
  const isConnected = use$(api.isConnected$);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const {
    data: sessions = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: ['external-sessions'],
    queryFn: () => api.getExternalSessions(),
    enabled: isConnected,
    staleTime: 30 * 1000,
  });

  const filtered = sessions.filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (s.session_name ?? '').toLowerCase().includes(q) ||
      s.session_id.toLowerCase().includes(q) ||
      (s.harness ?? '').toLowerCase().includes(q) ||
      (s.model ?? '').toLowerCase().includes(q) ||
      (s.project ?? '').toLowerCase().includes(q)
    );
  });

  const harnessGroups: Record<string, ExternalSessionCatalogItem[]> = {};
  for (const s of filtered) {
    const h = s.harness || 'unknown';
    if (!harnessGroups[h]) harnessGroups[h] = [];
    harnessGroups[h].push(s);
  }

  const isProviderUnavailable = ApiClientError.isApiError(error) && error.status === 503;

  if (isProviderUnavailable) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <AlertCircle className="h-12 w-12 text-muted-foreground" />
        <div>
          <h2 className="text-lg font-medium">External sessions unavailable</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The server does not have an external session provider configured. Install{' '}
            <code className="rounded bg-muted px-1 text-xs">gptme-sessions</code> into{' '}
            <strong>gptme&apos;s Python environment</strong> (not via{' '}
            <code className="rounded bg-muted px-1 text-xs">uv tool install</code>, which creates an
            isolated venv) and restart the server to enable this feature.
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <AlertCircle className="h-12 w-12 text-destructive" />
        <div>
          <h2 className="text-lg font-medium">Failed to load sessions</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {error instanceof Error ? error.message : 'An unexpected error occurred.'}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* List panel */}
      <div className={`flex flex-col border-r ${selectedId ? 'w-80 flex-shrink-0' : 'flex-1'}`}>
        {/* Header */}
        <div className="border-b px-4 py-3">
          <h1 className="text-base font-semibold">External Sessions</h1>
          <p className="text-xs text-muted-foreground">
            Read-only view of sessions from other harnesses (Claude Code, Codex, etc.)
          </p>
        </div>

        {/* Search */}
        <div className="border-b px-3 py-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-8 pl-8 text-sm"
              placeholder="Search sessions…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>

        {/* Sessions list */}
        <div className="flex-1 overflow-y-auto p-2">
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
              Loading sessions…
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-sm text-muted-foreground">
              <Bot className="h-8 w-8" />
              {search ? (
                <p>No sessions match &ldquo;{search}&rdquo;</p>
              ) : (
                <>
                  <p>No external sessions found</p>
                  <p className="text-xs">Sessions from the last 30 days will appear here</p>
                </>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {Object.entries(harnessGroups)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([harness, items]) => (
                  <div key={harness}>
                    <div className="mb-1 px-1 text-xs font-medium text-muted-foreground">
                      {HARNESS_LABELS[harness] ?? harness} ({items.length})
                    </div>
                    <div className="space-y-1">
                      {items.map((session) => (
                        <SessionCard
                          key={session.id}
                          session={session}
                          selected={selectedId === session.id}
                          onClick={() =>
                            setSelectedId(selectedId === session.id ? null : session.id)
                          }
                        />
                      ))}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* Footer stats */}
        {sessions.length > 0 && (
          <div className="border-t px-4 py-2 text-xs text-muted-foreground">
            {sessions.length} session{sessions.length !== 1 ? 's' : ''} · last 30 days
          </div>
        )}
      </div>

      {/* Detail panel */}
      {selectedId && (
        <div className="flex-1 overflow-hidden">
          <SessionDetail sessionId={selectedId} onClose={() => setSelectedId(null)} />
        </div>
      )}
    </div>
  );
};
