import { useCallback, useEffect, useState } from 'react';
import { Copy, QrCode } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';
import { invokeTauri } from '@/utils/tauri';

interface LanStatus {
  enabled: boolean;
  lan_ip: string | null;
  port: number;
  url: string | null;
  qr_svg: string | null;
}

export function LanAccessPanel() {
  const [status, setStatus] = useState<LanStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Restore state on mount (reload-safe)
  useEffect(() => {
    invokeTauri<LanStatus>('get_lan_access_status')
      .then(setStatus)
      .catch((e) => setError(String(e)));
  }, []);

  const handleToggle = useCallback(async (checked: boolean) => {
    setIsLoading(true);
    setError(null);
    try {
      if (checked) {
        const next = await invokeTauri<LanStatus>('enable_lan_access');
        setStatus(next);
        toast.success('LAN access enabled');
      } else {
        await invokeTauri('disable_lan_access');
        setStatus((prev) => (prev ? { ...prev, enabled: false, url: null, qr_svg: null } : prev));
        toast.success('LAN access disabled');
      }
    } catch (e) {
      setError(String(e));
      toast.error(`Failed: ${e}`);
      // The backend may have cleared LAN state even when the command errored
      // (disable clears state before returning the error) — refetch so the
      // toggle reflects reality instead of a stale "enabled".
      try {
        setStatus(await invokeTauri<LanStatus>('get_lan_access_status'));
      } catch {
        // status fetch failed; leave stale value
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleCopyUrl = useCallback(async () => {
    if (!status?.url) return;
    try {
      await navigator.clipboard.writeText(status.url);
      toast.success('URL copied to clipboard');
    } catch (e) {
      toast.error(`Failed to copy URL: ${e}`);
    }
  }, [status?.url]);

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-1 text-lg font-medium">LAN Access</h3>
        <p className="mb-4 text-sm text-muted-foreground">
          Share your gptme session with phones or tablets on the same Wi-Fi — scan the QR code to
          connect instantly.
        </p>
      </div>

      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <Label htmlFor="lan-toggle" className="text-sm">
            Enable LAN access
          </Label>
          <p className="text-xs text-muted-foreground">
            Makes gptme reachable on your local network
          </p>
        </div>
        <Switch
          id="lan-toggle"
          checked={status?.enabled ?? false}
          disabled={isLoading || status === null}
          onCheckedChange={handleToggle}
        />
      </div>

      {error && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>
      )}

      {status?.enabled && status.url && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <p className="flex-1 rounded-md bg-muted px-3 py-2 font-mono text-sm">{status.url}</p>
            <Button variant="ghost" size="icon" onClick={handleCopyUrl} title="Copy URL">
              <Copy className="h-4 w-4" />
            </Button>
          </div>

          <p className="text-xs text-muted-foreground">
            The QR code includes a session token — anyone who scans it gains access. Only use on
            trusted Wi-Fi.
          </p>

          {status.qr_svg ? (
            <div className="flex justify-center rounded-lg border bg-white p-4">
              <img
                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(status.qr_svg)}`}
                alt="QR code for LAN access"
                className="h-48 w-48"
              />
            </div>
          ) : (
            <div className="flex h-48 items-center justify-center rounded-lg border bg-muted/30">
              <QrCode className="h-12 w-12 text-muted-foreground/40" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
