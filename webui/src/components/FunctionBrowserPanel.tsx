import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Cpu, Loader2, RefreshCw, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Tool } from '@/types/tool';
import { useToolsApi } from '@/utils/toolsApi';

export function FunctionBrowserPanel() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [selectedToolName, setSelectedToolName] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { listTools } = useToolsApi();
  const fetchControllerRef = useRef<AbortController | null>(null);

  const loadTools = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const data = await listTools(signal);
        if (!signal?.aborted) {
          setTools(data);
          setLoading(false);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Failed to load tools');
        setLoading(false);
      }
    },
    [listTools]
  );

  const startLoad = useCallback(() => {
    fetchControllerRef.current?.abort();
    const controller = new AbortController();
    fetchControllerRef.current = controller;
    void loadTools(controller.signal);
  }, [loadTools]);

  useEffect(() => {
    startLoad();
    return () => fetchControllerRef.current?.abort();
  }, [startLoad]);

  const lowerQuery = query.toLowerCase();
  const filtered = useMemo(
    () =>
      tools.filter(
        (t) =>
          !lowerQuery ||
          t.name.toLowerCase().includes(lowerQuery) ||
          t.desc.toLowerCase().includes(lowerQuery) ||
          t.block_types.some((bt) => bt.toLowerCase().includes(lowerQuery))
      ),
    [tools, lowerQuery]
  );

  useEffect(() => {
    if (!filtered.length) {
      setSelectedToolName(null);
      return;
    }
    if (!selectedToolName || !filtered.some((t) => t.name === selectedToolName)) {
      setSelectedToolName(filtered[0].name);
    }
  }, [filtered, selectedToolName]);

  const selectedTool = filtered.find((t) => t.name === selectedToolName) ?? null;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b p-4">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <div>
            <h2 className="text-sm font-medium">Functions</h2>
            <p className="text-xs text-muted-foreground">
              {tools.length} tool{tools.length === 1 ? '' : 's'}
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={startLoad} title="Refresh">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Search */}
      <div className="border-b px-4 py-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            placeholder="Search tools…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-8 pl-8 text-sm"
          />
        </div>
      </div>

      {/* Split pane */}
      <div className="flex min-h-0 flex-1 flex-col">
        {/* Tool list */}
        <div className="max-h-[45%] w-full shrink-0 overflow-auto border-b">
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : error ? (
            <div className="flex h-full items-center justify-center p-4 text-center text-sm text-destructive">
              {error}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex h-full items-center justify-center p-4 text-center text-sm text-muted-foreground">
              {query ? 'No matching tools.' : 'No tools available.'}
            </div>
          ) : (
            <div className="divide-y">
              {filtered.map((tool) => {
                const isSelected = tool.name === selectedToolName;
                return (
                  <button
                    key={tool.name}
                    type="button"
                    onClick={() => setSelectedToolName(tool.name)}
                    className={`w-full px-3 py-2.5 text-left transition-colors hover:bg-muted/50 ${
                      isSelected ? 'bg-muted' : ''
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <span className="block truncate font-mono text-sm font-medium">
                          {tool.name}
                        </span>
                        {tool.desc && (
                          <span className="block truncate text-xs text-muted-foreground">
                            {tool.desc}
                          </span>
                        )}
                      </div>
                      {!tool.is_available && (
                        <Badge variant="outline" className="shrink-0 text-xs">
                          unavail
                        </Badge>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Tool detail */}
        <div className="min-h-0 w-full flex-1 overflow-auto p-4">
          {selectedTool ? (
            <ToolDetail tool={selectedTool} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Select a tool to see details
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ToolDetail({ tool }: { tool: Tool }) {
  return (
    <div className="space-y-4 text-sm">
      {/* Name + badges */}
      <div>
        <h3 className="font-mono text-base font-semibold">{tool.name}</h3>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {tool.is_mcp && (
            <Badge variant="secondary" className="text-xs">
              MCP
            </Badge>
          )}
          {!tool.is_available && (
            <Badge variant="destructive" className="text-xs">
              unavailable
            </Badge>
          )}
          {tool.disabled_by_default && (
            <Badge variant="outline" className="text-xs">
              opt-in
            </Badge>
          )}
          {tool.block_types.map((bt) => (
            <Badge key={bt} variant="outline" className="font-mono text-xs">
              {bt}
            </Badge>
          ))}
        </div>
      </div>

      {/* Description */}
      {tool.desc && (
        <div>
          <p className="text-muted-foreground">{tool.desc}</p>
        </div>
      )}

      {/* Parameters */}
      {tool.parameters.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Parameters
          </h4>
          <div className="space-y-2">
            {tool.parameters.map((p) => (
              <div key={p.name} className="rounded-md border bg-muted/30 px-3 py-2">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono font-medium">{p.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">{p.type}</span>
                  {p.required && <span className="text-xs text-destructive">required</span>}
                </div>
                {p.description && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{p.description}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Instructions */}
      {tool.instructions && (
        <div>
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Instructions
          </h4>
          <div className="rounded-md border bg-muted/30 px-3 py-2">
            <pre className="whitespace-pre-wrap text-xs leading-relaxed">{tool.instructions}</pre>
          </div>
        </div>
      )}

      {/* No instructions fallback */}
      {!tool.desc && !tool.instructions && tool.parameters.length === 0 && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Box className="h-4 w-4" />
          <span>No additional metadata available for this tool.</span>
        </div>
      )}
    </div>
  );
}
