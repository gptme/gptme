import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, AlertTriangle, RefreshCw, RotateCcw, Save } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useApi } from '@/contexts/ApiContext';

type ConfigFileResponse = {
  content: string;
  path: string;
  write_target: string;
  local_config_path: string;
  local_config_exists: boolean;
  local_overrides_main: boolean;
  status?: string;
};

type ConfigFileErrorResponse = {
  error?: string;
};

async function parseConfigFileResponse(
  response: Response,
  fallbackMessage: string
): Promise<ConfigFileResponse> {
  const data = (await response.json().catch(() => null)) as
    | ConfigFileResponse
    | ConfigFileErrorResponse
    | null;

  if (!response.ok) {
    throw new Error(data && 'error' in data && data.error ? data.error : fallbackMessage);
  }

  if (!data || !('content' in data)) {
    throw new Error(fallbackMessage);
  }

  return data;
}

export function ConfigFileEditor() {
  const { api } = useApi();
  const [configFile, setConfigFile] = useState<ConfigFileResponse | null>(null);
  const [content, setContent] = useState('');
  const [savedContent, setSavedContent] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestHeaders = useCallback(
    (json = false): Record<string, string> => {
      const headers: Record<string, string> = {};
      if (json) {
        headers['Content-Type'] = 'application/json';
      }
      if (api.authHeader) {
        headers.Authorization = api.authHeader;
      }
      return headers;
    },
    [api.authHeader]
  );

  const loadConfig = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${api.baseUrl}/api/v2/user/config-file`, {
        headers: requestHeaders(),
      });
      const data = await parseConfigFileResponse(response, 'Failed to load config file');
      setConfigFile(data);
      setContent(data.content);
      setSavedContent(data.content);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load config file';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [api.baseUrl, requestHeaders]);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  const handleSave = async () => {
    setIsSaving(true);
    setError(null);
    try {
      const response = await fetch(`${api.baseUrl}/api/v2/user/config-file`, {
        method: 'PUT',
        headers: requestHeaders(true),
        body: JSON.stringify({ content }),
      });
      const data = await parseConfigFileResponse(response, 'Failed to save config file');
      setConfigFile(data);
      setContent(data.content);
      setSavedContent(data.content);
      toast.success('Config file saved.');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save config file';
      setError(message);
      toast.error(message);
    } finally {
      setIsSaving(false);
    }
  };

  const hasChanges = content !== savedContent;
  const pathLabel = configFile?.write_target ?? configFile?.path ?? 'config.toml';

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">Raw config file</h4>
          <p className="mt-1 break-all text-xs text-muted-foreground">
            <code>{pathLabel}</code>
          </p>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={() => void loadConfig()}
              disabled={isLoading || isSaving}
            >
              <RefreshCw className="h-4 w-4" />
              <span className="sr-only">Refresh config file</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Refresh</TooltipContent>
        </Tooltip>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {configFile?.local_config_exists && configFile?.local_overrides_main && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            A local override config also exists at{' '}
            <code className="text-xs">{configFile.local_config_path}</code> and takes precedence.
            Changes saved here may be shadowed by values in the local file.
          </span>
        </div>
      )}

      {/\[\s*env\s*\]/.test(savedContent) && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This config contains an <code className="text-xs">[env]</code> section. API keys stored
            here are readable by anyone with access to this file. Use the <strong>API Keys</strong>{' '}
            panel to manage provider keys — they are stored in{' '}
            <code className="text-xs">config.local.toml</code> instead.
          </span>
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="settings-config-file-editor">TOML</Label>
        <Textarea
          id="settings-config-file-editor"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={isLoading || isSaving}
          spellCheck={false}
          className="min-h-72 resize-y font-mono text-xs leading-5"
          aria-label="gptme config TOML"
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {isLoading ? 'Loading config file…' : hasChanges ? 'Unsaved changes' : 'Saved'}
        </span>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => setContent(savedContent)}
            disabled={isLoading || isSaving || !hasChanges}
          >
            <RotateCcw className="mr-2 h-4 w-4" />
            Discard
          </Button>
          <Button
            type="button"
            onClick={() => void handleSave()}
            disabled={isLoading || isSaving || !hasChanges}
          >
            <Save className="mr-2 h-4 w-4" />
            {isSaving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  );
}
