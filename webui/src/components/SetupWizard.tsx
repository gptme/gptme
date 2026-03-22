import { useState } from 'react';
import { Button } from '@/components/ui/button';
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
import { use$ } from '@legendapp/state/react';
import { Monitor, Cloud, ArrowRight, Check, Terminal, ExternalLink } from 'lucide-react';

type SetupStep = 'welcome' | 'mode' | 'local' | 'cloud' | 'complete';

// The gptme cloud service is hosted on fleet.gptme.ai (the cloud.gptme.ai domain
// is a planned alias). Override with VITE_GPTME_CLOUD_BASE_URL for other deployments.
const CLOUD_AUTH_URL = import.meta.env.VITE_GPTME_CLOUD_BASE_URL
  ? `${import.meta.env.VITE_GPTME_CLOUD_BASE_URL}/authorize`
  : 'https://fleet.gptme.ai/authorize';

export function SetupWizard() {
  const { settings, updateSettings } = useSettings();
  const { isConnected$, connect } = useApi();
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
  const isTauri = isTauriEnvironment();

  const completeSetup = () => {
    updateSettings({ hasCompletedSetup: true });
  };

  // Close the dialog. Also calls completeSetup() so that skipping or finishing always persists.
  const closeWizard = () => {
    completeSetup();
    setIsOpen(false);
  };

  const handleLocalSetup = async () => {
    if (isConnected) {
      // Persist immediately so a refresh before clicking "Start using gptme" won't re-show wizard.
      completeSetup();
      setStep('complete');
      return;
    }
    setIsConnecting(true);
    setConnectError(null);
    try {
      await connect();
      // Persist immediately — isOpen remains true so the complete step renders.
      completeSetup();
      setStep('complete');
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
    // Persist immediately — isOpen stays true so the complete step renders.
    completeSetup();
    setStep('complete');
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
                className="flex items-start gap-4 rounded-lg border p-4 text-left transition-colors hover:bg-accent"
              >
                <Monitor className="mt-0.5 h-6 w-6 shrink-0" />
                <div>
                  <div className="font-medium">Local</div>
                  <div className="text-sm text-muted-foreground">
                    {isTauri
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
              <DialogTitle>Local setup</DialogTitle>
              <DialogDescription>
                {isTauri
                  ? 'The gptme server is managed automatically by the desktop app.'
                  : 'Start the gptme server to get going.'}
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-4 py-4">
              {isTauri ? (
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
                }}
              >
                Back
              </Button>
              <Button onClick={handleLocalSetup} disabled={isConnecting}>
                {isConnecting ? 'Connecting...' : isConnected ? 'Continue' : 'Connect'}
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
                {isTauri && (
                  <p className="text-xs text-muted-foreground">
                    The app will handle the login callback via the <code>gptme://</code> deep link.
                  </p>
                )}
              </div>
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button variant="outline" onClick={() => setStep('mode')}>
                Back
              </Button>
              <Button onClick={handleCloudLogin} className="gap-2">
                Sign in to gptme.ai
                <ExternalLink className="h-4 w-4" />
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
