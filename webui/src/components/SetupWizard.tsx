import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useSettings } from '@/contexts/SettingsContext';
import { useApi } from '@/contexts/ApiContext';
import { useTauriServerStatus } from '@/hooks/useTauriServerStatus';
import {
  API_KEY_PROVIDER_METADATA,
  API_KEY_PROVIDER_OPTIONS,
  type ApiKeyProvider,
} from '@/utils/apiKeyProviders';
import { fetchProviderConfigured } from '@/utils/providerStatus';
import { isTauriEnvironment, invokeTauri } from '@/utils/tauri';
import {
  bumpProviderStatusVersion,
  setupWizard$,
  type SetupWizardStep,
} from '@/stores/setupWizard';
import { use$ } from '@legendapp/state/react';
import { Monitor, Cloud, ArrowRight, Check, Terminal, ExternalLink } from 'lucide-react';

type SetupStep = SetupWizardStep;
type SetupModelInfo = {
  id: string;
  provider: string;
  model: string;
};
type SetupModelsResponse = {
  models: SetupModelInfo[];
  recommended: string[];
};

// The gptme cloud service is hosted on fleet.gptme.ai (the cloud.gptme.ai domain
// is a planned alias). Use a small runtime helper so Jest doesn't choke on import.meta.
function getCloudAuthUrl(): string {
  let cloudBaseUrl: string | undefined;

  try {
    cloudBaseUrl = Function('return import.meta.env.VITE_GPTME_CLOUD_BASE_URL')() as
      | string
      | undefined;
  } catch {
    cloudBaseUrl = undefined;
  }

  return `${cloudBaseUrl || 'https://fleet.gptme.ai'}/authorize`;
}

const CLOUD_AUTH_URL = getCloudAuthUrl();
const SERVER_START_RETRY_COUNT = 6;
const SERVER_START_RETRY_DELAY_MS = 250;
const SERVER_READY_RETRY_COUNT = 10;
const SERVER_READY_RETRY_DELAY_MS = 250;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function withAuthHeaders(
  authHeader: string | null,
  headers: Record<string, string> = {}
): Record<string, string> {
  return authHeader ? { ...headers, Authorization: authHeader } : headers;
}

export function SetupWizard() {
  const { settings, updateSettings } = useSettings();
  const { api, isConnected$, connect, connectionConfig } = useApi();
  const isConnected = use$(isConnected$);
  const [step, setStep] = useState<SetupStep>('welcome');
  // isOpen is a one-time snapshot of hasCompletedSetup taken at mount. It is intentionally
  // NOT derived from settings on subsequent renders — the wizard manages its own visibility
  // via setIsOpen after mount. This prevents external changes to hasCompletedSetup (e.g. from
  // resetSettings) from re-opening the wizard mid-session.
  // Also avoids React batching issues: completeSetup() + setStep('complete') update two separate
  // state atoms in the same event handler; a derived signal would close the dialog before the
  // 'complete' step could render.
  const [isOpen, setIsOpen] = useState(!settings.hasCompletedSetup);
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [cloudLoginStarted, setCloudLoginStarted] = useState(false);
  const lastAutoAdvanceBaseUrlRef = useRef<string | null>(null);
  const isTauri = isTauriEnvironment();
  const { isLoading: isLoadingTauriStatus, managesLocalServer } = useTauriServerStatus();
  const externalOpen = use$(setupWizard$.open);
  const externalStep = use$(setupWizard$.step);
  const isRemoteOnlyTauri = isTauri && managesLocalServer === false;
  const isDeterminingTauriMode = isTauri && isLoadingTauriStatus;
  const canManageApiKeyInApp = isTauri && managesLocalServer === true;
  const [remoteBaseUrl, setRemoteBaseUrl] = useState(
    connectionConfig.baseUrl === 'http://127.0.0.1:5700' ? '' : connectionConfig.baseUrl
  );
  const [remoteAuthToken, setRemoteAuthToken] = useState(connectionConfig.authToken || '');
  const [apiKeyProvider, setApiKeyProvider] = useState<ApiKeyProvider>('anthropic');
  const [apiKey, setApiKey] = useState('');
  const [apiKeyModel, setApiKeyModel] = useState('');
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const [apiKeyError, setApiKeyError] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<SetupModelInfo[]>([]);
  const [recommendedModels, setRecommendedModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  const completeSetup = useCallback(() => {
    updateSettings({ hasCompletedSetup: true });
    bumpProviderStatusVersion();
  }, [updateSettings]);

  const checkProviderConfigured = useCallback(
    (signal?: AbortSignal) =>
      fetchProviderConfigured(connectionConfig.baseUrl, api.authHeader, signal),
    [api.authHeader, connectionConfig.baseUrl]
  );

  const saveApiKeyToServer = useCallback(
    async (provider: ApiKeyProvider, apiKeyValue: string, model?: string) => {
      const resp = await fetch(`${connectionConfig.baseUrl}/api/v2/user/api-key`, {
        method: 'POST',
        headers: withAuthHeaders(api.authHeader, {
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          provider,
          api_key: apiKeyValue,
          ...(model ? { model } : {}),
        }),
      });

      if (resp.ok) {
        return;
      }

      let message = `Failed to save API key (${resp.status})`;
      try {
        const data = (await resp.json()) as { error?: string };
        if (data.error) {
          message = data.error;
        }
      } catch {
        // Fall back to the generic status-based message.
      }
      throw new Error(message);
    },
    [api.authHeader, connectionConfig.baseUrl]
  );

  const fetchAvailableModels = useCallback(async () => {
    setModelsLoading(true);
    setModelsError(null);
    try {
      const resp = await fetch(`${connectionConfig.baseUrl}/api/v2/models`, {
        headers: withAuthHeaders(api.authHeader),
      });
      if (!resp.ok) {
        throw new Error(`Failed to load models (${resp.status})`);
      }

      const data = (await resp.json()) as SetupModelsResponse;
      setAvailableModels(data.models || []);
      setRecommendedModels(data.recommended || []);
    } catch (error) {
      setAvailableModels([]);
      setRecommendedModels([]);
      setModelsError(error instanceof Error ? error.message : 'Failed to load available models.');
    } finally {
      setModelsLoading(false);
    }
  }, [api.authHeader, connectionConfig.baseUrl]);

  const providerModels = useMemo(
    () => availableModels.filter((model) => model.provider === apiKeyProvider),
    [apiKeyProvider, availableModels]
  );

  useEffect(() => {
    if (step !== 'provider' || !canManageApiKeyInApp) {
      return;
    }
    void fetchAvailableModels();
  }, [canManageApiKeyInApp, fetchAvailableModels, step]);

  useEffect(() => {
    if (step !== 'provider' || !canManageApiKeyInApp) {
      return;
    }
    if (providerModels.length === 0) {
      setApiKeyModel('');
      return;
    }
    const preferredModel =
      providerModels.find((model) => recommendedModels.includes(model.id)) || providerModels[0];
    setApiKeyModel((current) =>
      providerModels.some((model) => model.id === current) ? current : preferredModel.id
    );
  }, [canManageApiKeyInApp, providerModels, recommendedModels, step]);

  // Fetch /api/v2, check provider_configured, then advance to 'provider' or 'complete'.
  const checkProviderAndAdvance = useCallback(
    async ({ assumeConfiguredOnError = true }: { assumeConfiguredOnError?: boolean } = {}) => {
      try {
        if (!(await checkProviderConfigured())) {
          setStep('provider');
          return;
        }
      } catch (error) {
        if (!assumeConfiguredOnError) {
          throw error;
        }
        // On error, don't block the user — assume provider is configured.
      }
      completeSetup();
      setStep('complete');
    },
    [checkProviderConfigured, completeSetup]
  );

  const startServerWithRetry = useCallback(async () => {
    let lastError: unknown;

    for (let attempt = 0; attempt < SERVER_START_RETRY_COUNT; attempt += 1) {
      try {
        await invokeTauri('start_server');
        return;
      } catch (error) {
        lastError = error;
        const message = error instanceof Error ? error.message : String(error);
        const shouldRetry =
          /port\s+\d+\s+is already in use/i.test(message) || /already in use/i.test(message);

        if (!shouldRetry || attempt === SERVER_START_RETRY_COUNT - 1) {
          throw error;
        }

        await sleep(SERVER_START_RETRY_DELAY_MS);
      }
    }

    throw lastError instanceof Error ? lastError : new Error('Failed to start gptme-server');
  }, []);

  const waitForRestartedServer = useCallback(async () => {
    for (let attempt = 0; attempt < SERVER_READY_RETRY_COUNT; attempt += 1) {
      try {
        if (await checkProviderConfigured()) {
          completeSetup();
          setStep('complete');
          return;
        }

        setStep('provider');
        return;
      } catch {
        if (attempt === SERVER_READY_RETRY_COUNT - 1) {
          throw new Error(
            'Saved the API key, but the server did not come back in time. Retry in a few seconds.'
          );
        }
        await sleep(SERVER_READY_RETRY_DELAY_MS);
      }
    }
  }, [checkProviderConfigured, completeSetup]);

  useEffect(() => {
    if (!isConnected) {
      lastAutoAdvanceBaseUrlRef.current = null;
      return;
    }
    if (!isOpen || step === 'complete' || step === 'provider') return;

    if (lastAutoAdvanceBaseUrlRef.current === connectionConfig.baseUrl) return;
    lastAutoAdvanceBaseUrlRef.current = connectionConfig.baseUrl;

    setCloudLoginStarted(false);
    void checkProviderAndAdvance();
  }, [checkProviderAndAdvance, connectionConfig.baseUrl, isConnected, isOpen, step]);

  useEffect(() => {
    if (!externalOpen) {
      return;
    }

    setConnectError(null);
    setCloudLoginStarted(false);
    setApiKeyError(null);
    lastAutoAdvanceBaseUrlRef.current = null;
    setStep(externalStep);
    setIsOpen(true);
    setupWizard$.open.set(false);
  }, [externalOpen, externalStep]);

  // Close the dialog. Also calls completeSetup() so that skipping or finishing always persists.
  const closeWizard = () => {
    completeSetup();
    setIsOpen(false);
  };

  const handleLocalSetup = async () => {
    if (isConnected) {
      // Already connected — check provider config and advance.
      void checkProviderAndAdvance();
      return;
    }
    setIsConnecting(true);
    setConnectError(null);
    try {
      await connect();
      // The isConnected useEffect will fire and call checkProviderAndAdvance.
    } catch (err) {
      setConnectError(
        err instanceof Error ? err.message : 'Could not connect to server. Is it running?'
      );
    } finally {
      setIsConnecting(false);
    }
  };

  // Save the user's API key through the server surface, then use the Tauri
  // shell only for the desktop-specific restart of the managed sidecar.
  const handleSaveApiKey = async () => {
    const trimmed = apiKey.trim();
    if (!trimmed) {
      setApiKeyError('Enter an API key before saving.');
      return;
    }
    if (!modelsLoading && providerModels.length > 0 && !apiKeyModel) {
      setApiKeyError('Choose a default model before saving.');
      return;
    }
    setApiKeySaving(true);
    setApiKeyError(null);
    try {
      await saveApiKeyToServer(apiKeyProvider, trimmed, apiKeyModel || undefined);
      try {
        await invokeTauri('stop_server');
      } catch {
        // No running server is fine; start_server will still launch one.
      }
      await startServerWithRetry();
      setApiKey('');
      lastAutoAdvanceBaseUrlRef.current = null;
      await waitForRestartedServer();
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : String(err));
    } finally {
      setApiKeySaving(false);
    }
  };

  const handleManualProviderCheck = async () => {
    setApiKeyError(null);
    try {
      await checkProviderAndAdvance({ assumeConfiguredOnError: false });
    } catch (err) {
      setApiKeyError(
        err instanceof Error
          ? err.message
          : 'Could not verify provider configuration. Try again in a few seconds.'
      );
    }
  };

  const handleCloudLogin = () => {
    // Open the cloud auth URL — the deep-link flow (gptme://) or URL fragment
    // will handle the callback and connect automatically.
    window.open(CLOUD_AUTH_URL, '_blank');
    setConnectError(null);
    setCloudLoginStarted(true);
  };

  const handleRemoteSetup = async () => {
    const trimmedBaseUrl = remoteBaseUrl.trim();
    const trimmedAuthToken = remoteAuthToken.trim();

    if (!trimmedBaseUrl) {
      setConnectError('Enter the URL of the gptme server you want to use.');
      return;
    }

    let normalizedBaseUrl: string;
    try {
      const parsedUrl = new URL(trimmedBaseUrl);
      if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
        throw new Error('Remote server URLs must use http:// or https://');
      }
      normalizedBaseUrl = parsedUrl.toString().replace(/\/+$/, '');
    } catch (error) {
      setConnectError(error instanceof Error ? error.message : 'Enter a valid server URL.');
      return;
    }

    setIsConnecting(true);
    setConnectError(null);
    try {
      await connect({
        baseUrl: normalizedBaseUrl,
        authToken: trimmedAuthToken || null,
        useAuthToken: Boolean(trimmedAuthToken),
      });
      completeSetup();
      setStep('complete');
    } catch (err) {
      setConnectError(
        err instanceof Error ? err.message : 'Could not connect to the remote gptme server.'
      );
    } finally {
      setIsConnecting(false);
    }
  };

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) {
          closeWizard();
        }
      }}
    >
      <DialogContent
        className="sm:max-w-md"
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={closeWizard}
      >
        {step === 'welcome' && (
          <>
            <DialogHeader>
              <DialogTitle className="text-center text-2xl">Welcome to gptme</DialogTitle>
              <DialogDescription className="text-center">
                Your AI assistant for the terminal and beyond. Let&apos;s get you set up.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col items-center gap-4 py-4">
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <Terminal className="h-5 w-5" />
                <span>Write code, run commands, manage files</span>
              </div>
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <Monitor className="h-5 w-5" />
                <span>Works locally or in the cloud</span>
              </div>
            </div>
            <DialogFooter className="flex-col-reverse gap-2 sm:flex-row sm:justify-center">
              <Button variant="ghost" onClick={closeWizard}>
                Skip for now
              </Button>
              <Button onClick={() => setStep('mode')} className="gap-2">
                Get started
                <ArrowRight className="h-4 w-4" />
              </Button>
            </DialogFooter>
          </>
        )}

        {step === 'mode' && (
          <>
            <DialogHeader>
              <DialogTitle>Choose your setup</DialogTitle>
              <DialogDescription>How would you like to use gptme?</DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-3 py-4">
              <button
                onClick={() => setStep('local')}
                disabled={isDeterminingTauriMode}
                className="flex items-start gap-4 rounded-lg border p-4 text-left transition-colors hover:bg-accent"
              >
                <Monitor className="mt-0.5 h-6 w-6 shrink-0" />
                <div>
                  <div className="font-medium">
                    {isDeterminingTauriMode
                      ? 'Checking environment'
                      : isRemoteOnlyTauri
                        ? 'Remote server'
                        : 'Local'}
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {isDeterminingTauriMode
                      ? 'Checking whether this build manages a local gptme server.'
                      : isRemoteOnlyTauri
                        ? 'Connect to Bob or another self-hosted gptme instance by URL. No on-device server.'
                        : managesLocalServer
                          ? 'Run gptme on your machine. The server starts automatically.'
                          : 'Run gptme-server on your machine. Bring your own API keys.'}
                  </div>
                </div>
              </button>
              <button
                onClick={() => setStep('cloud')}
                className="flex items-start gap-4 rounded-lg border p-4 text-left transition-colors hover:bg-accent"
              >
                <Cloud className="mt-0.5 h-6 w-6 shrink-0" />
                <div>
                  <div className="font-medium">Cloud</div>
                  <div className="text-sm text-muted-foreground">
                    Connect to gptme.ai for a managed experience. No setup required.
                  </div>
                </div>
              </button>
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={closeWizard}>
                Skip for now
              </Button>
            </DialogFooter>
          </>
        )}

        {step === 'local' && (
          <>
            <DialogHeader>
              <DialogTitle>{isRemoteOnlyTauri ? 'Remote server setup' : 'Local setup'}</DialogTitle>
              <DialogDescription>
                {isDeterminingTauriMode
                  ? 'Checking whether this build manages a local gptme server.'
                  : isRemoteOnlyTauri
                    ? 'Connect this app to Bob, gptme.ai, or another remote gptme server.'
                    : managesLocalServer
                      ? 'The gptme server is managed automatically by the desktop app.'
                      : 'Start the gptme server to get going.'}
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4 py-4">
              {isDeterminingTauriMode ? (
                <p className="text-sm text-muted-foreground">
                  Checking whether this build manages a local gptme server...
                </p>
              ) : isRemoteOnlyTauri ? (
                <div className="flex flex-col gap-3">
                  <p className="text-sm text-muted-foreground">
                    Enter the URL for a remote gptme server. Use Cloud instead if you want the
                    managed <code>gptme.ai</code> sign-in flow.
                  </p>
                  <Input
                    autoComplete="url"
                    placeholder="https://bob.example.com"
                    value={remoteBaseUrl}
                    onChange={(event) => setRemoteBaseUrl(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !isConnecting) {
                        event.preventDefault();
                        void handleRemoteSetup();
                      }
                    }}
                  />
                  <Input
                    autoComplete="off"
                    placeholder="Optional API token"
                    type="password"
                    value={remoteAuthToken}
                    onChange={(event) => setRemoteAuthToken(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !isConnecting) {
                        event.preventDefault();
                        void handleRemoteSetup();
                      }
                    }}
                  />
                  <p className="text-xs text-muted-foreground">
                    For Bob or other self-hosted servers, paste the base URL and token here.
                  </p>
                </div>
              ) : managesLocalServer ? (
                <div className="flex items-start gap-3 rounded-lg bg-muted p-4">
                  <Check className="mt-0.5 h-5 w-5 shrink-0 text-green-500" />
                  <div className="text-sm">
                    <p className="font-medium">Server starts automatically</p>
                    <p className="mt-1 text-muted-foreground">
                      The desktop app manages the gptme server for you. Just click connect below.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <p className="text-sm text-muted-foreground">
                    Start the server in your terminal:
                  </p>
                  <code className="rounded bg-muted px-3 py-2 font-mono text-sm">
                    pipx run --spec &apos;gptme[server]&apos; gptme-server
                  </code>
                  <p className="text-xs text-muted-foreground">
                    Or if you have gptme installed:{' '}
                    <code className="rounded bg-muted px-1">gptme-server</code>
                  </p>
                </div>
              )}
              {isConnected && (
                <div className="flex items-center gap-2 text-sm text-green-500">
                  <Check className="h-4 w-4" />
                  Connected to server
                </div>
              )}
              {connectError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {connectError}
                </div>
              )}
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                variant="outline"
                onClick={() => {
                  setStep('mode');
                  setConnectError(null);
                  setCloudLoginStarted(false);
                }}
              >
                Back
              </Button>
              <Button
                onClick={isRemoteOnlyTauri ? handleRemoteSetup : handleLocalSetup}
                disabled={isConnecting || isDeterminingTauriMode}
              >
                {isDeterminingTauriMode
                  ? 'Checking...'
                  : isConnecting
                    ? 'Connecting...'
                    : isConnected
                      ? 'Continue'
                      : 'Connect'}
              </Button>
            </DialogFooter>
          </>
        )}

        {step === 'cloud' && (
          <>
            <DialogHeader>
              <DialogTitle>Cloud setup</DialogTitle>
              <DialogDescription>
                Sign in to gptme.ai for a fully managed experience.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4 py-4">
              <div className="flex flex-col gap-3">
                <p className="text-sm text-muted-foreground">
                  You&apos;ll be redirected to gptme.ai to sign in. After authentication,
                  you&apos;ll be connected automatically.
                </p>
                {cloudLoginStarted && !isConnected && (
                  <div className="rounded-lg border border-border/70 bg-muted px-3 py-2 text-sm text-muted-foreground">
                    Waiting for sign-in to complete… This window will update automatically once the
                    app connects.
                  </div>
                )}
                {connectError && (
                  <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                    {connectError}
                  </div>
                )}
                {isTauri && (
                  <p className="text-xs text-muted-foreground">
                    The app will handle the login callback via the <code>gptme://</code> deep link.
                  </p>
                )}
              </div>
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                variant="outline"
                onClick={() => {
                  setStep('mode');
                  setCloudLoginStarted(false);
                  setConnectError(null);
                }}
              >
                Back
              </Button>
              <Button onClick={handleCloudLogin} className="gap-2">
                Sign in to gptme.ai
                <ExternalLink className="h-4 w-4" />
              </Button>
            </DialogFooter>
          </>
        )}

        {step === 'provider' && (
          <>
            <DialogHeader>
              <DialogTitle>Configure a provider</DialogTitle>
              <DialogDescription>
                The server is running, but it does not have an LLM provider yet.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4 py-4">
              <p className="text-sm text-muted-foreground">
                Choose one of these paths to finish setup:
              </p>
              <div className="rounded-lg border bg-muted/40 p-4 text-sm">
                <p className="font-medium">Use gptme.ai</p>
                <p className="mt-1 text-muted-foreground">
                  Sign in with a cloud account for a managed setup with no local API keys.
                </p>
              </div>
              <div className="rounded-lg border bg-muted/40 p-4 text-sm">
                <p className="font-medium">Bring your own API key</p>
                {canManageApiKeyInApp ? (
                  <div className="mt-3 flex flex-col gap-3">
                    <p className="text-muted-foreground">
                      Paste an API key and we&apos;ll save it and restart the server.
                    </p>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor="setup-api-key-provider">Provider</Label>
                      <select
                        id="setup-api-key-provider"
                        className="h-9 rounded-md border bg-background px-3 text-sm"
                        value={apiKeyProvider}
                        onChange={(e) => setApiKeyProvider(e.target.value as ApiKeyProvider)}
                        disabled={apiKeySaving}
                      >
                        {API_KEY_PROVIDER_OPTIONS.map((provider) => (
                          <option key={provider.value} value={provider.value}>
                            {provider.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor="setup-api-key-model">Default model</Label>
                      <select
                        id="setup-api-key-model"
                        className="h-9 rounded-md border bg-background px-3 text-sm"
                        value={apiKeyModel}
                        onChange={(e) => setApiKeyModel(e.target.value)}
                        disabled={apiKeySaving || modelsLoading || providerModels.length === 0}
                      >
                        {providerModels.length === 0 ? (
                          <option value="">
                            {modelsLoading ? 'Loading models…' : 'No models available'}
                          </option>
                        ) : (
                          providerModels.map((model) => (
                            <option key={model.id} value={model.id}>
                              {model.model}
                            </option>
                          ))
                        )}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        Pick the model gptme should use after restart.
                      </p>
                      {modelsError && (
                        <p className="text-xs text-destructive">
                          {modelsError} If this keeps failing, save without a model and gptme will
                          fall back to the provider default.
                        </p>
                      )}
                    </div>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor="setup-api-key-input">API key</Label>
                      <Input
                        id="setup-api-key-input"
                        type="password"
                        autoComplete="off"
                        spellCheck={false}
                        placeholder={API_KEY_PROVIDER_METADATA[apiKeyProvider].placeholder}
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !apiKeySaving && apiKey.trim().length > 0) {
                            e.preventDefault();
                            void handleSaveApiKey();
                          }
                        }}
                        disabled={apiKeySaving}
                      />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Supports{' '}
                      {API_KEY_PROVIDER_OPTIONS.map((provider) => provider.label).join(', ')}. Azure
                      OpenAI still needs manual configuration because it also requires deployment
                      settings.
                    </p>
                    {apiKeyError && (
                      <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                        {apiKeyError}
                      </div>
                    )}
                    <Button
                      onClick={() => void handleSaveApiKey()}
                      disabled={apiKeySaving || apiKey.trim().length === 0}
                    >
                      {apiKeySaving ? 'Saving…' : 'Save and restart server'}
                    </Button>
                  </div>
                ) : (
                  <>
                    <p className="mt-1 text-muted-foreground">
                      Set one of these environment variables before launching gptme-server:
                    </p>
                    <div className="mt-3 flex flex-col gap-2">
                      <code className="rounded bg-muted px-3 py-2 font-mono text-sm">
                        ANTHROPIC_API_KEY=sk-ant-...
                      </code>
                      <code className="rounded bg-muted px-3 py-2 font-mono text-sm">
                        OPENAI_API_KEY=sk-...
                      </code>
                    </div>
                  </>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Get a key from{' '}
                <a
                  href="https://console.anthropic.com"
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2"
                >
                  Anthropic Console
                </a>{' '}
                or{' '}
                <a
                  href="https://platform.openai.com"
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2"
                >
                  OpenAI Platform
                </a>
                .{' '}
                {canManageApiKeyInApp
                  ? 'Saving a key restarts the server automatically.'
                  : 'Then restart the server and check again.'}
              </p>
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button variant="outline" onClick={() => setStep('cloud')}>
                Use gptme.ai instead
              </Button>
              <Button variant="outline" onClick={() => void handleManualProviderCheck()}>
                I configured a provider
              </Button>
              <Button variant="ghost" onClick={closeWizard}>
                Skip for now
              </Button>
            </DialogFooter>
          </>
        )}

        {step === 'complete' && (
          <>
            <DialogHeader>
              <DialogTitle className="text-center">You&apos;re all set!</DialogTitle>
              <DialogDescription className="text-center">
                {isConnected
                  ? 'Connected and ready to go. Start a conversation to begin.'
                  : 'You can connect to a server anytime from the connection button.'}
              </DialogDescription>
            </DialogHeader>
            <div className="flex justify-center py-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                <Check className="h-8 w-8 text-primary" />
              </div>
            </div>
            <DialogFooter className="sm:justify-center">
              <Button onClick={closeWizard} className="gap-2">
                Start using gptme
                <ArrowRight className="h-4 w-4" />
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
