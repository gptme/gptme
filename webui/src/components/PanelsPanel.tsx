/**
 * Right-sidebar panel that lists and renders iframe panels declared by tools
 * in message metadata (``panel_hints``). Each entry is rendered as a full-height
 * ``SandboxedIframePanel`` tab when selected.
 *
 * Phase 3b of the webui artifact surface (#830).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FC } from 'react';
import { LayoutDashboard, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SandboxedIframePanel } from '@/components/SandboxedIframePanel';
import type { IframePanelEntry } from '@/types/panels';
import type { IframePanelDescriptor } from '@/types/panel';
import { usePanelsApi } from '@/utils/panelsApi';

interface PanelsPanelProps {
  conversationId: string;
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

export const PanelsPanel: FC<PanelsPanelProps> = ({ conversationId }) => {
  const [panels, setPanels] = useState<IframePanelEntry[]>([]);
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
          Tools can declare iframe panels via{' '}
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
              className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
                selected?.id === p.id
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
              }`}
            >
              {p.title}
            </button>
          ))}
        </div>
      )}

      {/* Iframe content */}
      <div className="min-h-0 flex-1">
        {selected && (
          <SandboxedIframePanel
            descriptor={toDescriptor(selected)}
            conversationId={conversationId}
          />
        )}
      </div>
    </div>
  );
};
