import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useApi } from '@/contexts/ApiContext';
import { useModels } from '@/hooks/useModels';
import { useUserSettings } from '@/hooks/useUserSettings';
import {
  API_KEY_PROVIDER_METADATA,
  API_KEY_PROVIDER_OPTIONS,
  type ApiKeyProvider,
} from '@/utils/apiKeyProviders';
import { toast } from 'sonner';

type SaveApiKeyResponse = {
  status: string;
  provider: string;
  env_var: string;
  restart_required: boolean;
};

export function ServerApiKeySettings() {
  const { api } = useApi();
  const { models, recommendedModels, isLoading: modelsLoading, error: modelsError } = useModels();
  const { settings, refetch: refetchSettings } = useUserSettings();
  const [provider, setProvider] = useState<ApiKeyProvider>('anthropic');
  const [selectedModel, setSelectedModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const providerModels = useMemo(
    () => models.filter((model) => model.provider === provider),
    [models, provider]
  );
  const configuredProviders = settings?.providers_configured ?? [];

  useEffect(() => {
    if (providerModels.length === 0) {
      setSelectedModel('');
      return;
    }
    const preferredModel =
      providerModels.find((model) => recommendedModels.includes(model.id)) || providerModels[0];
    setSelectedModel((current) =>
      providerModels.some((model) => model.id === current) ? current : preferredModel.id
    );
  }, [providerModels, recommendedModels]);

  const handleSave = async () => {
    const trimmedApiKey = apiKey.trim();
    if (!trimmedApiKey) {
      toast.error('Enter an API key before saving.');
      return;
    }

    setIsSaving(true);
    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (api.authHeader) {
        headers.Authorization = api.authHeader;
      }

      const response = await fetch(`${api.baseUrl}/api/v2/user/api-key`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          provider,
          api_key: trimmedApiKey,
          ...(selectedModel ? { model: selectedModel } : {}),
        }),
      });

      let data: SaveApiKeyResponse | { error?: string } | null = null;
      try {
        data = (await response.json()) as SaveApiKeyResponse | { error?: string };
      } catch {
        data = null;
      }
      if (!response.ok) {
        throw new Error(
          data && 'error' in data && data.error ? data.error : 'Failed to save API key'
        );
      }

      const result = data as SaveApiKeyResponse | null;
      setApiKey('');
      refetchSettings();
      toast.success(
        result?.restart_required === false
          ? 'API key saved.'
          : 'API key saved. Restart the server to apply it.'
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save API key');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div>
        <h4 className="text-sm font-medium">Provider API key</h4>
        <p className="text-sm text-muted-foreground">
          Save a provider key into the server config without editing files by hand.
        </p>
      </div>

      {configuredProviders.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {configuredProviders.map((configuredProvider) => (
            <span
              key={configuredProvider}
              className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
            >
              {configuredProvider}
            </span>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor="settings-api-key-provider">Provider</Label>
        <select
          id="settings-api-key-provider"
          className="h-9 rounded-md border bg-background px-3 text-sm"
          value={provider}
          onChange={(event) => setProvider(event.target.value as ApiKeyProvider)}
          disabled={isSaving}
        >
          {API_KEY_PROVIDER_OPTIONS.map((providerOption) => (
            <option key={providerOption.value} value={providerOption.value}>
              {providerOption.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="settings-api-key-model">Default model</Label>
        <select
          id="settings-api-key-model"
          className="h-9 rounded-md border bg-background px-3 text-sm"
          value={selectedModel}
          onChange={(event) => setSelectedModel(event.target.value)}
          disabled={isSaving || modelsLoading || providerModels.length === 0}
        >
          {providerModels.length === 0 ? (
            <option value="">{modelsLoading ? 'Loading models…' : 'No models available'}</option>
          ) : (
            providerModels.map((model) => (
              <option key={model.id} value={model.id}>
                {model.model}
              </option>
            ))
          )}
        </select>
        <p className="text-xs text-muted-foreground">
          Optional. Saving a model also updates the server default.
        </p>
        {modelsError && <p className="text-xs text-destructive">{modelsError}</p>}
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="settings-api-key-input">API key</Label>
        <Input
          id="settings-api-key-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={API_KEY_PROVIDER_METADATA[provider].placeholder}
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          disabled={isSaving}
        />
      </div>

      <Button
        type="button"
        variant="outline"
        onClick={() => void handleSave()}
        disabled={isSaving || apiKey.trim().length === 0}
      >
        {isSaving ? 'Saving…' : 'Save API key'}
      </Button>
    </div>
  );
}
