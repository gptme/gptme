import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useApi } from '@/contexts/ApiContext';

type ProviderHealthStatus = 'ok' | 'configured' | 'error';

type ProviderHealthEntry = {
  status: ProviderHealthStatus;
  latency_ms: number | null;
  error: string | null;
};

type ProviderHealthResponse = {
  providers: Record<string, ProviderHealthEntry>;
};

const STATUS_STYLES: Record<
  ProviderHealthStatus,
  { label: string; dotClassName: string; textClassName: string }
> = {
  ok: {
    label: 'Reachable',
    dotClassName: 'bg-emerald-500',
    textClassName: 'text-emerald-700 dark:text-emerald-400',
  },
  configured: {
    label: 'Configured',
    dotClassName: 'bg-sky-500',
    textClassName: 'text-sky-700 dark:text-sky-400',
  },
  error: {
    label: 'Error',
    dotClassName: 'bg-red-500',
    textClassName: 'text-red-700 dark:text-red-400',
  },
};

export function ServerProviderHealthSettings() {
  const { api } = useApi();
  const [health, setHealth] = useState<ProviderHealthResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadHealth = useCallback(
    async (force = false) => {
      setIsLoading(true);
      setError(null);

      try {
        const headers: Record<string, string> = {};
        if (api.authHeader) {
          headers.Authorization = api.authHeader;
        }

        const suffix = force ? '?force=1' : '';
        const response = await fetch(`${api.baseUrl}/api/v2/providers/health${suffix}`, {
          headers,
        });

        if (!response.ok) {
          throw new Error(`Failed to fetch provider health: ${response.statusText}`);
        }

        const data = (await response.json()) as ProviderHealthResponse;
        setHealth(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch provider health');
      } finally {
        setIsLoading(false);
      }
    },
    [api.authHeader, api.baseUrl]
  );

  useEffect(() => {
    void loadHealth();
  }, [loadHealth]);

  const providerEntries = Object.entries(health?.providers ?? {});

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-medium">Provider health</h4>
          <p className="text-sm text-muted-foreground">
            Check which configured providers are actually reachable from this server.
          </p>
        </div>

        <Button type="button" variant="outline" size="sm" onClick={() => void loadHealth(true)}>
          <RefreshCw className={`mr-2 h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          {isLoading ? 'Refreshing…' : 'Refresh'}
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {!error && isLoading && !health ? (
        <p className="text-sm text-muted-foreground">Loading provider health…</p>
      ) : null}

      {!error && !isLoading && providerEntries.length == 0 ? (
        <p className="text-sm text-muted-foreground">No configured providers detected.</p>
      ) : null}

      {providerEntries.length > 0 ? (
        <div className="space-y-2">
          {providerEntries.map(([provider, info]) => {
            const styles = STATUS_STYLES[info.status] ?? {
              label: info.status,
              dotClassName: 'bg-gray-500',
              textClassName: 'text-gray-500',
            };
            return (
              <div
                key={provider}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 rounded-full ${styles.dotClassName}`} />
                    <span className="font-medium">{provider}</span>
                    <span className={`text-xs font-medium ${styles.textClassName}`}>
                      {styles.label}
                    </span>
                  </div>
                  {info.error ? (
                    <p className="mt-1 text-xs text-muted-foreground">{info.error}</p>
                  ) : null}
                </div>

                <div className="text-xs text-muted-foreground">
                  {info.latency_ms == null ? 'No latency' : `${info.latency_ms} ms`}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
