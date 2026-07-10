import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Volume2, Palette, Info, FileText, ExternalLink, Server, Rocket } from 'lucide-react';
import { useSettings } from '@/contexts/SettingsContext';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';
import { ServerConfiguration } from '@/components/settings/ServerConfiguration';
import { DeveloperDeploy } from '@/components/settings/DeveloperDeploy';
import { type SettingsCategory } from '@/stores/settingsModal';
import { setupWizard$ } from '@/stores/setupWizard';
import { getPrimaryClient } from '@/stores/serverClients';
import { isSpeechSupported } from '@/utils/tts';

export interface SettingsContentProps {
  activeCategory: SettingsCategory;
  onCategoryChange: (category: SettingsCategory) => void;
  /** Called when the settings view should close (no-op in full-page mode). */
  onClose?: () => void;
}

const categories = [
  {
    id: 'servers' as const,
    label: 'Servers',
    icon: Server,
    description: 'Manage server connections',
  },
  {
    id: 'appearance' as const,
    label: 'Appearance',
    icon: Palette,
    description: 'Theme and visual preferences',
  },
  {
    id: 'audio' as const,
    label: 'Audio',
    icon: Volume2,
    description: 'Sound and notification settings',
  },
  {
    id: 'content' as const,
    label: 'Content',
    icon: FileText,
    description: 'Message and code display options',
  },
  ...(import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEV_TOOLS === 'true'
    ? [
        {
          id: 'developer' as const,
          label: 'Developer',
          icon: Rocket,
          description: 'Deploy and debugging actions',
        },
      ]
    : []),
  {
    id: 'about' as const,
    label: 'About',
    icon: Info,
    description: 'Version info and links',
  },
];

export function SettingsContent({
  activeCategory,
  onCategoryChange,
  onClose,
}: SettingsContentProps) {
  const { settings, updateSettings, resetSettings } = useSettings();
  const { theme, setTheme } = useTheme();
  const [serverVersion, setServerVersion] = useState<string | null>(null);

  // Fetch server version when the 'about' tab becomes active
  useEffect(() => {
    if (activeCategory !== 'about') return;
    const client = getPrimaryClient();
    if (!client) return;
    client
      .getServerInfo()
      .then((info) => setServerVersion(info.version ?? null))
      .catch(() => setServerVersion(null));
  }, [activeCategory]);

  const renderCategoryContent = () => {
    switch (activeCategory) {
      case 'servers':
        return <ServerConfiguration />;

      case 'appearance':
        return (
          <div className="space-y-6">
            <div>
              <h3 className="mb-1 text-lg font-medium">Appearance</h3>
              <p className="mb-4 text-sm text-muted-foreground">
                Customize the visual appearance of the application
              </p>
            </div>

            <div className="space-y-3">
              <Label className="text-sm text-muted-foreground">Theme</Label>
              <div className="flex items-center space-x-1 rounded-md bg-muted/50 p-1">
                {[
                  { value: 'light', label: 'Light' },
                  { value: 'dark', label: 'Dark' },
                  { value: 'system', label: 'System' },
                ].map((option) => {
                  const isActive = theme === option.value;
                  return (
                    <Button
                      key={option.value}
                      variant={isActive ? 'secondary' : 'ghost'}
                      size="sm"
                      className={`flex-1 text-sm ${
                        isActive ? 'bg-background shadow-sm' : 'hover:bg-background/60'
                      }`}
                      onClick={() => setTheme(option.value as 'light' | 'dark' | 'system')}
                    >
                      {option.label}
                    </Button>
                  );
                })}
              </div>
            </div>

            <Separator />

            <div className="space-y-3">
              <Label className="text-sm text-muted-foreground">Welcome Background</Label>
              <p className="text-xs text-muted-foreground">
                Image URL or CSS gradient for the new-chat view. The card gets a frosted glass
                effect when a background is set.
              </p>
              <input
                type="text"
                value={settings.welcomeBackground}
                onChange={(e) => updateSettings({ welcomeBackground: e.target.value })}
                placeholder="e.g. linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <div className="flex flex-wrap gap-2">
                {[
                  { label: 'Sunset', value: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)' },
                  {
                    label: 'Ocean',
                    value: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                  },
                  {
                    label: 'Forest',
                    value: 'linear-gradient(135deg, #11998e 0%, #38ef7d 100%)',
                  },
                  {
                    label: 'Night',
                    value: 'linear-gradient(135deg, #0c0c1d 0%, #1a1a3e 50%, #2d1b69 100%)',
                  },
                ].map((preset) => (
                  <Button
                    key={preset.label}
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    onClick={() => updateSettings({ welcomeBackground: preset.value })}
                  >
                    {preset.label}
                  </Button>
                ))}
                {settings.welcomeBackground && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs text-muted-foreground"
                    onClick={() => updateSettings({ welcomeBackground: '' })}
                  >
                    Clear
                  </Button>
                )}
              </div>
            </div>
          </div>
        );

      case 'audio':
        return (
          <div className="space-y-6">
            <div>
              <h3 className="mb-1 text-lg font-medium">Audio</h3>
              <p className="mb-4 text-sm text-muted-foreground">
                Configure sound and notification preferences
              </p>
            </div>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="chime-toggle" className="text-sm">
                  Completion chime
                </Label>
                <p className="text-xs text-muted-foreground">
                  Play sound when agent completes tasks
                </p>
              </div>
              <Switch
                id="chime-toggle"
                checked={settings.chimeEnabled}
                onCheckedChange={(checked) => updateSettings({ chimeEnabled: checked })}
              />
            </div>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="tts-toggle" className="text-sm">
                  Read responses aloud
                </Label>
                <p className="text-xs text-muted-foreground">
                  Auto-play assistant messages aloud using the TTS engine selected below
                </p>
              </div>
              <Switch
                id="tts-toggle"
                checked={settings.ttsEnabled}
                disabled={!isSpeechSupported()}
                onCheckedChange={(checked) => updateSettings({ ttsEnabled: checked })}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="tts-engine" className="text-sm">
                TTS engine
              </Label>
              <p className="text-xs text-muted-foreground">Which engine synthesizes speech.</p>
              <Select
                value={settings.ttsProvider}
                onValueChange={(value) =>
                  updateSettings({ ttsProvider: value as typeof settings.ttsProvider })
                }
              >
                <SelectTrigger id="tts-engine">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Automatic (server → gptme-tts → browser)</SelectItem>
                  <SelectItem value="server">gptme-server (provider-backed)</SelectItem>
                  <SelectItem value="external">gptme-tts server</SelectItem>
                  <SelectItem value="browser">Browser (Web Speech API)</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {settings.ttsProvider === 'server' &&
                  'Uses the connected gptme-server /api/v2/audio/speech endpoint (provider-backed, e.g. OpenRouter — higher quality). Requires OPENROUTER_API_KEY to be set on the server. For local TTS without an API key, use the gptme-tts server option instead.'}
                {settings.ttsProvider === 'external' &&
                  'Uses a standalone gptme-tts server for local TTS (no API key needed). Install with: pip install gptme-tts, then run: gptme-tts.'}
                {settings.ttsProvider === 'browser' &&
                  "Uses the browser's built-in speech synthesis (always available, no setup required)."}
                {settings.ttsProvider === 'auto' &&
                  'Tries the gptme-server endpoint (requires OPENROUTER_API_KEY), then a gptme-tts server (if URL is set below), then the browser.'}
              </p>
            </div>

            {(settings.ttsProvider === 'external' || settings.ttsProvider === 'auto') && (
              <div className="space-y-2">
                <Label htmlFor="tts-server-url" className="text-sm">
                  gptme-tts server URL
                </Label>
                <p className="text-xs text-muted-foreground">
                  HTTP base URL of a running{' '}
                  <code className="rounded bg-muted px-1">gptme-tts</code> server, e.g.{' '}
                  <code className="rounded bg-muted px-1">http://localhost:5001</code>.
                  {settings.ttsProvider === 'auto' && ' Leave empty to skip this engine.'}
                </p>
                <Input
                  id="tts-server-url"
                  type="text"
                  placeholder="http://localhost:5001"
                  value={settings.ttsServerUrl}
                  onChange={(e) => updateSettings({ ttsServerUrl: e.target.value.trim() })}
                />
              </div>
            )}

            <Separator />

            <div className="space-y-2">
              <Label htmlFor="stt-engine" className="text-sm">
                Speech-to-text engine
              </Label>
              <p className="text-xs text-muted-foreground">
                Choose which engine transcribes your microphone input for dictation.
              </p>
              <Select
                value={settings.sttProvider}
                onValueChange={(value: 'browser' | 'server') =>
                  updateSettings({ sttProvider: value })
                }
              >
                <SelectTrigger id="stt-engine" className="w-full">
                  <SelectValue placeholder="Select STT engine" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="browser">Browser (SpeechRecognition API)</SelectItem>
                  <SelectItem value="server">Server (OpenRouter / API)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <Separator />

            <div className="space-y-2">
              <Label htmlFor="voice-server-url" className="text-sm">
                Voice server URL
              </Label>
              <p className="text-xs text-muted-foreground">
                WebSocket URL of a running gptme-voice-server instance, e.g.{' '}
                <code className="rounded bg-muted px-1">ws://localhost:5700/voice</code>. Leave
                empty to hide the voice button.
              </p>
              <Input
                id="voice-server-url"
                type="text"
                placeholder="ws://localhost:5700/voice"
                value={settings.voiceServerUrl}
                onChange={(e) => updateSettings({ voiceServerUrl: e.target.value.trim() })}
              />
            </div>
          </div>
        );

      case 'content':
        return (
          <div className="space-y-6">
            <div>
              <h3 className="mb-1 text-lg font-medium">Content</h3>
              <p className="mb-4 text-sm text-muted-foreground">
                Control how messages and code are displayed
              </p>
            </div>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="blocks-toggle" className="text-sm">
                  Expand tool blocks by default
                </Label>
                <p className="text-xs text-muted-foreground">
                  By default, tool-use and output blocks are collapsed and code is shown. Enable to
                  expand every block.
                </p>
              </div>
              <Switch
                id="blocks-toggle"
                checked={settings.blocksDefaultOpen}
                onCheckedChange={(checked) => updateSettings({ blocksDefaultOpen: checked })}
              />
            </div>

            <Separator />

            <div className="space-y-4">
              <h4 className="text-sm font-medium">Tool Execution</h4>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label htmlFor="no-confirm-toggle" className="text-sm">
                    No-confirm mode
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    Auto-confirm all tool execution prompts without asking (equivalent to{' '}
                    <code className="font-mono">--no-confirm</code> in the CLI)
                  </p>
                </div>
                <Switch
                  id="no-confirm-toggle"
                  checked={settings.noConfirmMode}
                  onCheckedChange={(checked) => updateSettings({ noConfirmMode: checked })}
                />
              </div>
            </div>

            <Separator />

            <div className="space-y-4">
              <h4 className="text-sm font-medium">Developer Options</h4>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label htmlFor="hidden-toggle" className="text-sm">
                    Show hidden messages
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    Display messages marked as hidden (e.g., lessons, context)
                  </p>
                </div>
                <Switch
                  id="hidden-toggle"
                  checked={settings.showHiddenMessages}
                  onCheckedChange={(checked) => updateSettings({ showHiddenMessages: checked })}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label htmlFor="initial-system-toggle" className="text-sm">
                    Show initial system messages
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    Display the initial system prompt at the start of conversations
                  </p>
                </div>
                <Switch
                  id="initial-system-toggle"
                  checked={settings.showInitialSystem}
                  onCheckedChange={(checked) => updateSettings({ showInitialSystem: checked })}
                />
              </div>
            </div>
          </div>
        );

      case 'developer':
        return <DeveloperDeploy />;

      case 'about':
        return (
          <div className="space-y-6">
            <div>
              <h3 className="mb-1 text-lg font-medium">About</h3>
              <p className="mb-4 text-sm text-muted-foreground">
                Information about gptme and this web interface
              </p>
            </div>

            <div className="space-y-4">
              {serverVersion && (
                <div className="space-y-1">
                  <h4 className="text-sm font-medium">Version</h4>
                  <p className="font-mono text-sm text-muted-foreground">gptme {serverVersion}</p>
                </div>
              )}

              <div className="space-y-3">
                <h4 className="text-sm font-medium">Related Projects</h4>
                <div className="flex flex-col space-y-2">
                  <a
                    href="https://github.com/gptme/gptme"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center text-sm text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <ExternalLink className="mr-2 h-3 w-3" />
                    gptme - AI agent framework
                  </a>
                  <a
                    href="https://github.com/gptme/gptme/tree/master/webui"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center text-sm text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <ExternalLink className="mr-2 h-3 w-3" />
                    gptme-webui - Web interface
                  </a>
                </div>
              </div>

              <Separator />

              <div className="space-y-2">
                <h4 className="text-sm font-medium">Setup Wizard</h4>
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted-foreground">
                    Re-run the setup wizard to change server or sign in to gptme.ai
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      onClose?.();
                      setupWizard$.step.set('welcome');
                      setupWizard$.open.set(true);
                    }}
                  >
                    Re-run Setup
                  </Button>
                </div>
              </div>

              <Separator />

              <div className="space-y-2">
                <h4 className="text-sm font-medium">Reset Settings</h4>
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted-foreground">
                    Restore all settings to their default values
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      resetSettings();
                      setTheme('system');
                      onClose?.();
                    }}
                  >
                    Reset All
                  </Button>
                </div>
              </div>
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col sm:flex-row">
      {/* Mobile: horizontal tab strip */}
      <div className="flex overflow-x-auto border-b bg-muted/20 p-2 sm:hidden">
        {categories.map((category) => {
          const Icon = category.icon;
          const isActive = activeCategory === category.id;
          return (
            <button
              key={category.id}
              onClick={() => onCategoryChange(category.id)}
              className={cn(
                'flex shrink-0 flex-col items-center gap-1 rounded-md px-3 py-2 text-xs transition-colors',
                isActive ? 'border bg-background shadow-sm' : 'hover:bg-muted/60'
              )}
            >
              <Icon className="h-4 w-4" />
              <span className="font-medium">{category.label}</span>
            </button>
          );
        })}
      </div>

      {/* Desktop: vertical sidebar */}
      <div className="hidden w-48 overflow-y-auto border-r bg-muted/20 p-4 sm:block">
        <nav className="space-y-1">
          {categories.map((category) => {
            const Icon = category.icon;
            const isActive = activeCategory === category.id;

            return (
              <button
                key={category.id}
                onClick={() => onCategoryChange(category.id)}
                className={cn(
                  'flex w-full items-start gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                  isActive ? 'border bg-background shadow-sm' : 'hover:bg-muted/60'
                )}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="text-left">
                  <div className="font-medium">{category.label}</div>
                  <div className="text-xs text-muted-foreground">{category.description}</div>
                </div>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Content */}
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4 sm:p-6">
        {renderCategoryContent()}
      </div>
    </div>
  );
}
