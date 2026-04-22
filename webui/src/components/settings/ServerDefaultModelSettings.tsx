import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { ModelPickerButton } from '@/components/ModelPicker';
import { useApi } from '@/contexts/ApiContext';
import { useModels } from '@/hooks/useModels';
import { useUserSettings } from '@/hooks/useUserSettings';
import { toast } from 'sonner';

type SaveDefaultModelResponse = {
  status: string;
  model: string;
  restart_required: boolean;
};

export function ServerDefaultModelSettings() {
  const { api } = useApi();
  const { models, recommendedModels, isLoading: modelsLoading, error: modelsError } = useModels();
  const {
    settings,
    isLoading: settingsLoading,
    error: settingsError,
    refetch: refetchSettings,
  } = useUserSettings();
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [isSaving, setIsSaving] = useState(false);

  const isLoading = modelsLoading || settingsLoading;
  const error = modelsError || settingsError;

  // Server-authoritative default model from /api/v2/user/settings
  const serverDefaultModel = settings?.default_model ?? null;

  const fallbackModel = useMemo(() => {
    if (models.length === 0) {
      return '';
    }
    return (
      recommendedModels.find((modelId) => models.some((model) => model.id === modelId)) ||
      models[0].id
    );
  }, [models, recommendedModels]);

  useEffect(() => {
    if (serverDefaultModel) {
      setSelectedModel((current) => current || serverDefaultModel);
      return;
    }
    if (!selectedModel && fallbackModel) {
      setSelectedModel(fallbackModel);
    }
  }, [serverDefaultModel, fallbackModel, selectedModel]);

  const handleSave = async () => {
    if (!selectedModel) {
      toast.error('Select a model first.');
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

      const response = await fetch(`${api.baseUrl}/api/v2/user/default-model`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ model: selectedModel }),
      });

      const data = (await response.json()) as SaveDefaultModelResponse | { error?: string };
      if (!response.ok) {
        throw new Error('error' in data && data.error ? data.error : 'Failed to save model');
      }

      const result = data as SaveDefaultModelResponse;
      // Refetch from server to reflect the saved state authoritatively
      refetchSettings();
      toast.success(
        result.restart_required
          ? 'Default model saved. Restart the server to apply it.'
          : 'Default model updated.'
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save model');
    } finally {
      setIsSaving(false);
    }
  };

  const configuredProviders = settings?.providers_configured ?? [];

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div>
        <h4 className="text-sm font-medium">Default model</h4>
        <p className="text-sm text-muted-foreground">
          Choose which model new server-backed chats should use by default.
        </p>
      </div>

      {configuredProviders.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {configuredProviders.map((provider) => (
            <span
              key={provider}
              className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
            >
              {provider}
            </span>
          ))}
        </div>
      )}

      {serverDefaultModel && (
        <p className="text-xs text-muted-foreground">
          Current default: <code>{serverDefaultModel}</code>
        </p>
      )}

      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        <ModelPickerButton
          value={selectedModel}
          onSelect={setSelectedModel}
          disabled={isLoading || isSaving || models.length === 0}
          placeholder={isLoading ? 'Loading models…' : 'Select model'}
        />
      )}

      <Button
        type="button"
        variant="outline"
        onClick={() => void handleSave()}
        disabled={isSaving || isLoading || !selectedModel}
      >
        {isSaving ? 'Saving…' : 'Save default model'}
      </Button>
    </div>
  );
}
