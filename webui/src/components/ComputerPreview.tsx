import { useState, useEffect, useRef, useCallback } from 'react';
import { RefreshCw, Monitor, Wifi, WifiOff, ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useApi } from '@/contexts/ApiContext';
import type { FC } from 'react';

const VNC_URL = 'http://localhost:6080/vnc.html';
const DEFAULT_POLL_INTERVAL_MS = 2000;

interface BackendStatus {
  screenshot_available: boolean;
  system: string;
  display: string | null;
  backends: Record<string, boolean>;
}

/**
 * Live computer desktop preview panel for issue #216.
 *
 * Polls /api/v2/computer/screenshot for a live screenshot of the desktop —
 * a lightweight alternative to VNC that works without Docker/noVNC.
 * Falls back to a VNC iframe link for Docker-based computer-use deployments.
 */
export const ComputerPreview: FC = () => {
  const { api, connectionConfig } = useApi();
  const baseUrl = connectionConfig.baseUrl.replace(/\/+$/, '');

  const [screenshotSrc, setScreenshotSrc] = useState<string | null>(null);
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPolling, setIsPolling] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [showVnc, setShowVnc] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevSrcRef = useRef<string | null>(null);
  // Ref so schedulePoll always reads the latest isPolling without stale-closure issues
  const isPollingRef = useRef(true);
  // AbortController for the in-flight screenshot fetch — cancelled on unmount
  const abortControllerRef = useRef<AbortController | null>(null);

  // Keep isPollingRef in sync so timer callbacks see current pause state
  useEffect(() => {
    isPollingRef.current = isPolling;
  }, [isPolling]);

  const fetchStatus = useCallback(async () => {
    const headers: Record<string, string> = {};
    if (api.authHeader) headers.Authorization = api.authHeader;
    try {
      const resp = await fetch(`${baseUrl}/api/v2/computer/status`, { headers });
      if (resp.ok) {
        const data = await resp.json();
        setStatus(data);
      }
    } catch {
      // Status fetch failure is non-fatal
    }
  }, [baseUrl, api.authHeader]);

  const fetchScreenshot = useCallback(async () => {
    abortControllerRef.current?.abort();
    const ac = new AbortController();
    abortControllerRef.current = ac;

    const headers: Record<string, string> = {};
    if (api.authHeader) headers.Authorization = api.authHeader;
    try {
      const resp = await fetch(`${baseUrl}/api/v2/computer/screenshot?quality=75`, {
        headers,
        cache: 'no-store',
        signal: ac.signal,
      });

      if (resp.status === 503) {
        const data = await resp.json();
        setError(data.error || 'Screenshot backend unavailable');
        setIsLoading(false);
        return;
      }

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
        setError(data.error || `HTTP ${resp.status}`);
        setIsLoading(false);
        return;
      }

      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);

      // Revoke previous blob URL to prevent memory leak
      if (prevSrcRef.current) {
        URL.revokeObjectURL(prevSrcRef.current);
      }
      prevSrcRef.current = url;

      setScreenshotSrc(url);
      setError(null);
      setIsLoading(false);
      setLastUpdated(new Date());
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Failed to fetch screenshot');
      setIsLoading(false);
    }
  }, [baseUrl, api.authHeader]);

  // Reads isPolling from the ref so recursive .then() chains always see the
  // current pause state — avoids the stale-closure bug where clicking Pause
  // while a fetch is in-flight leaves the loop running indefinitely.
  const schedulePoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
    }
    if (isPollingRef.current) {
      pollTimerRef.current = setTimeout(() => {
        fetchScreenshot().then(schedulePoll);
      }, DEFAULT_POLL_INTERVAL_MS);
    }
  }, [fetchScreenshot]);

  // Fetch status once on mount
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  // Start polling when component mounts — intentionally run once only

  useEffect(() => {
    fetchScreenshot().then(schedulePoll);
    return () => {
      abortControllerRef.current?.abort();
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
      }
      if (prevSrcRef.current) {
        URL.revokeObjectURL(prevSrcRef.current);
      }
    };
  }, []);

  // Restart polling when isPolling changes
  useEffect(() => {
    if (isPolling) {
      schedulePoll();
    } else if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
    }
  }, [isPolling, schedulePoll]);

  const handleRefresh = () => {
    setIsLoading(true);
    fetchScreenshot().then(schedulePoll);
  };

  const togglePolling = () => setIsPolling((p) => !p);

  if (showVnc) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 border-b p-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowVnc(false)}
            title="Back to screenshot view"
          >
            <Monitor className="h-4 w-4" />
            <span className="ml-1 text-xs">Screenshot</span>
          </Button>
          <span className="text-xs text-muted-foreground">VNC mode (requires Docker)</span>
        </div>
        <iframe
          src={VNC_URL}
          className="h-full w-full rounded-md border-0"
          allow="clipboard-read; clipboard-write"
          title="VNC Viewer"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-1 border-b p-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={isLoading}
          title="Refresh screenshot"
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={togglePolling}
          title={isPolling ? 'Pause live view' : 'Resume live view'}
        >
          {isPolling ? (
            <Wifi className="h-4 w-4 text-green-500" />
          ) : (
            <WifiOff className="h-4 w-4 text-muted-foreground" />
          )}
        </Button>
        <div className="flex-1" />
        {lastUpdated && (
          <span className="text-xs text-muted-foreground">{lastUpdated.toLocaleTimeString()}</span>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowVnc(true)}
          title="Switch to VNC viewer (requires Docker computer-use service)"
        >
          <ExternalLink className="h-4 w-4" />
          <span className="ml-1 text-xs">VNC</span>
        </Button>
      </div>

      {/* Status bar */}
      {status && (
        <div className="flex flex-wrap gap-2 bg-muted/30 px-2 py-1 text-xs text-muted-foreground">
          <span>{status.system}</span>
          {status.display && <span>DISPLAY={status.display}</span>}
          {Object.entries(status.backends)
            .filter(([, available]) => available)
            .map(([name]) => (
              <span
                key={name}
                className="rounded bg-green-100 px-1 text-green-700 dark:bg-green-900/20 dark:text-green-400"
              >
                {name}
              </span>
            ))}
        </div>
      )}

      {/* Main content */}
      <div className="relative flex flex-1 items-center justify-center overflow-auto bg-black/5 p-2">
        {error ? (
          <div className="text-center text-sm">
            <WifiOff className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
            <p className="mb-2 text-muted-foreground">{error}</p>
            <p className="mb-3 text-xs text-muted-foreground">
              Start gptme-server on a machine with a desktop and{' '}
              <code className="rounded bg-muted px-1">--tools +computer</code>
            </p>
            <Button variant="outline" size="sm" onClick={() => setShowVnc(true)}>
              <ExternalLink className="mr-1 h-3 w-3" />
              Try VNC viewer
            </Button>
          </div>
        ) : isLoading ? (
          <div className="text-center text-sm">
            <Monitor className="mx-auto mb-2 h-8 w-8 animate-pulse text-muted-foreground" />
            <p className="text-xs text-muted-foreground">Connecting to desktop…</p>
          </div>
        ) : screenshotSrc ? (
          <img
            src={screenshotSrc}
            alt="Desktop screenshot"
            className="max-h-full max-w-full rounded object-contain shadow-sm"
            style={{ imageRendering: 'pixelated' }}
          />
        ) : null}
      </div>
    </div>
  );
};
