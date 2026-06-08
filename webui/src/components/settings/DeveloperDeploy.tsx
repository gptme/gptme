import { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, Rocket } from 'lucide-react';
import { toast } from 'sonner';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { getPrimaryClient } from '@/stores/serverClients';
import {
  getStagingDeployStatus,
  triggerStagingDeploy,
  type DeployStatus,
  type DeployTriggerResponse,
} from '@/utils/deployApi';

export function DeveloperDeploy() {
  const [status, setStatus] = useState<DeployStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [result, setResult] = useState<DeployTriggerResponse | null>(null);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [isDeploying, setIsDeploying] = useState(false);

  useEffect(() => {
    let cancelled = false;

    try {
      const api = getPrimaryClient();
      getStagingDeployStatus(api)
        .then((nextStatus) => {
          if (!cancelled) {
            setStatus(nextStatus);
            setStatusError(null);
          }
        })
        .catch((error) => {
          if (!cancelled) {
            setStatus(null);
            setStatusError(error instanceof Error ? error.message : 'Deploy endpoint unavailable');
          }
        });
    } catch (error) {
      if (!cancelled) {
        setStatus(null);
        setStatusError(error instanceof Error ? error.message : 'No server configured');
      }
    }

    return () => {
      cancelled = true;
    };
  }, []);

  const handleDeploy = async () => {
    setIsDeploying(true);
    setDeployError(null);
    setResult(null);

    try {
      const api = getPrimaryClient();
      const response = await triggerStagingDeploy(api);
      setResult(response);
      toast.success('Staging deploy queued');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to trigger staging deploy';
      setDeployError(message);
      toast.error(message);
    } finally {
      setIsDeploying(false);
    }
  };

  const targetLabel = status
    ? `${status.repository} / ${status.workflow || 'workflow unset'} @ ${status.ref}`
    : 'primary gptme server';

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-1 text-lg font-medium">Developer</h3>
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-muted-foreground">Staging deploy controls</p>
          {status && (
            <Badge variant={status.configured ? 'secondary' : 'outline'}>
              {status.configured ? 'Configured' : 'Not configured'}
            </Badge>
          )}
          {statusError && <Badge variant="outline">Endpoint unavailable</Badge>}
        </div>
      </div>

      <div className="space-y-3 rounded-md border p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <h4 className="text-sm font-medium">Deploy to staging</h4>
            <p className="text-xs text-muted-foreground">{targetLabel}</p>
          </div>
          <Button
            type="button"
            onClick={handleDeploy}
            disabled={isDeploying}
            className="w-full sm:w-auto"
          >
            {isDeploying ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Rocket className="mr-2 h-4 w-4" />
            )}
            {isDeploying ? 'Triggering' : 'Deploy'}
          </Button>
        </div>

        {statusError && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Deploy endpoint unavailable</AlertTitle>
            <AlertDescription>{statusError}</AlertDescription>
          </Alert>
        )}

        {deployError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Deploy failed</AlertTitle>
            <AlertDescription>{deployError}</AlertDescription>
          </Alert>
        )}

        {result && (
          <Alert>
            <CheckCircle2 className="h-4 w-4" />
            <AlertTitle>{result.message}</AlertTitle>
            <AlertDescription>
              <a
                href={result.actions_url}
                target="_blank"
                rel="noopener noreferrer"
                className="underline-offset-4 hover:underline"
              >
                Open GitHub Actions
              </a>
            </AlertDescription>
          </Alert>
        )}
      </div>
    </div>
  );
}
