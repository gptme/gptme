import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { isTauriEnvironment } from '@/utils/tauri';
import { useTauriServerStatus } from '@/hooks/useTauriServerStatus';
import { use$ } from '@legendapp/state/react';
import { Monitor, Cloud, ArrowRight, Check, Terminal, ExternalLink } from 'lucide-react';

type SetupStep = 'welcome' | 'mode' | 'local' | 'cloud' | 'provider' | 'complete';

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

export function SetupWizard() {
  const { settings, updateSettings } = useSettings();
  const { isConnected$, connect, connectionConfig } = useApi();
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
  const isRemoteOnlyTauri = isTauri && managesLocalServer === false;
  const isDeterminingTauriMode = isTauri && isLoadingTauriStatus;
  const [remoteBaseUrl, setRemoteBaseUrl] = useState(
    connectionConfig.baseUrl === 'http://127.0.0.1:5700' ? '' : connectionConfig.baseUrl
  );
  const [remoteAuthToken, setRemoteAuthToken] = useState(connectionConfig.authToken || '');

  const completeSetup = useCallback(() => {
    updateSettings({ hasCompletedSetup: true });
  }, [updateSettings]);

  // Fetch /api/v2, check provider_configured, then advance to 'provider' or 'complete'.
  const checkProviderAndAdvance = useCallback(async () => {
    try {
      const resp = await fetch(`${connectionConfig.baseUrl}/api/v2`);
      const data = (await resp.json()) as { provider_configured?: boolean };
      if (data.provider_configured === false) {
        setStep('provider');
        return;
      }
    } catch {
      // On error, don't block the user — assume provider is configured.
    }
    completeSetup();
    setStep('complete');
  }, [connectionConfig.baseUrl, completeSetup]);

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
    <Dialog open={isOpen} onOpenChange={() => {}}>
      <DialogContent
        className="sm:max-w-md [&>button]:hidden"
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
            <DialogFooter className="sm:justify-center">
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
                  />
                  <Input
                    autoComplete="off"
                    placeholder="Optional API token"
                    type="password"
                    value={remoteAuthToken}
                    onChange={(event) => setRemoteAuthToken(event.target.value)}
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
                . Then {isTauri ? 'restart the app' : 'restart the server'} and check again.
              </p>
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button variant="outline" onClick={() => setStep('cloud')}>
                Use gptme.ai instead
              </Button>
              <Button variant="outline" onClick={() => void checkProviderAndAdvance()}>
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
