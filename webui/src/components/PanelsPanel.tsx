/**
 * Right-sidebar panel that lists and renders iframe panels declared by tools
 * in message metadata (``panel_hints``). Each entry is rendered as a full-height
 * ``SandboxedIframePanel`` tab when selected.
 *
 * Phase 3b of the webui artifact surface (#830).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FC } from 'react';
import { LayoutDashboard, Loader2, Monitor, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SandboxedIframePanel } from '@/components/SandboxedIframePanel';
import type { PanelEntry, LiveAppPanelEntry, IframePanelEntry } from '@/types/panels';
import type { IframePanelDescriptor } from '@/types/panel';
import { usePanelsApi } from '@/utils/panelsApi';

interface PanelsPanelProps {
  conversationId: string;
}

/** Status indicator color for a live app panel. */
function statusColor(status: string): string {
  switch (status) {
    case 'running':
      return 'text-green-500';
    case 'error':
      return 'text-destructive';
    case 'stopped':
      return 'text-muted-foreground';
    default:
      return 'text-yellow-500';
  }
}

function toDescriptor(entry: IframePanelEntry): IframePanelDescriptor {
  return {
    id: entry.id,
    kind: 'iframe',
    title: entry.title,
    src: entry.src,
    sandbox: entry.sandbox,
    allow: entry.allow ?? undefined,
    resize: entry.resize ?? undefined,
    bootstrap: entry.bootstrap ?? undefined,
    icon: entry.icon ?? undefined,
  };
}

function isLiveApp(entry: PanelEntry): entry is LiveAppPanelEntry {
  return entry.kind === 'live_app';
}

function isIframe(entry: PanelEntry): entry is IframePanelEntry {
  return entry.kind === 'iframe';
}

export const PanelsPanel: FC<PanelsPanelProps> = ({ conversationId }) => {
  const [panels, setPanels] = useState<PanelEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { listPanels } = usePanelsApi();
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const data = await listPanels(conversationId, signal);
        if (!signal?.aborted) {
          setPanels(data);
          setLoading(false);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Failed to load panels');
        setLoading(false);
      }
    },
    [conversationId, listPanels]
  );

  useEffect(() => {
    const ctrl = new AbortController();
    controllerRef.current = ctrl;
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const handleRefresh = useCallback(() => {
    controllerRef.current?.abort();
    const ctrl = new AbortController();
    controllerRef.current = ctrl;
    load(ctrl.signal);
  }, [load]);

  const selected = panels.find((p) => p.id === selectedId) ?? panels[0] ?? null;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
        <p className="text-sm text-destructive">{error}</p>
        <Button variant="outline" size="sm" onClick={handleRefresh}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Retry
        </Button>
      </div>
    );
  }

  if (panels.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
        <LayoutDashboard className="h-8 w-8 opacity-40" />
        <p className="font-medium text-foreground">No panels</p>
        <p>
          Tools can declare iframe or live app panels via{' '}
          <code className="rounded bg-muted px-1">panel_hints</code> in message metadata.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Tab row — only shown when more than one panel is declared */}
      {panels.length > 1 && (
        <div className="flex shrink-0 gap-1 border-b px-2 py-1">
          {panels.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setSelectedId(p.id)}
              className={`flex items-center gap-1.5 rounded px-2 py-1 text-xs font-medium transition-colors ${
                selected?.id === p.id
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
              }`}
            >
              <span>{p.title}</span>
              {isLiveApp(p) && (
                <span className={`h-1.5 w-1.5 rounded-full ${statusColor(p.status)}`} />
              )}
            </button>
          ))}
        </div>
      )}

      {/* Panel content area */}
      <div className="min-h-0 flex-1">
        {selected && isLiveApp(selected) && selected.status !== 'running' && (
          <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm">
            <Monitor className="h-8 w-8 opacity-40" />
            <div className="space-y-1">
              <p className="font-medium text-foreground">{selected.title}</p>
              <p className={`${statusColor(selected.status)}`}>
                Status: <span className="capitalize">{selected.status}</span>
              </p>
              {selected.url && selected.status === 'stopped' && (
                <p className="text-muted-foreground">{selected.url}</p>
              )}
            </div>
          </div>
        )}
        {selected && isIframe(selected) && (
          <SandboxedIframePanel
            descriptor={toDescriptor(selected)}
            conversationId={conversationId}
          />
        )}
        {selected && isLiveApp(selected) && selected.status === 'running' && (
          <SandboxedIframePanel
            descriptor={{
              id: selected.id,
              kind: 'iframe',
              title: selected.title,
              src: selected.url ?? selected.src,
              sandbox: selected.sandbox,
              resize: 'auto',
            }}
            conversationId={conversationId}
          />
        )}
      </div>
    </div>
  );
};
