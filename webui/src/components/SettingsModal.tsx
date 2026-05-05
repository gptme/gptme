import { useState, forwardRef, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import { Settings, Volume2, Palette, Info, FileText, ExternalLink, Server } from 'lucide-react';
import { useSettings } from '@/contexts/SettingsContext';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';
import { ServerConfiguration } from '@/components/settings/ServerConfiguration';
import { use$ } from '@legendapp/state/react';
import { settingsModal$, type SettingsCategory } from '@/stores/settingsModal';
import { setupWizard$ } from '@/stores/setupWizard';
import { getPrimaryClient } from '@/stores/serverClients';
export type { SettingsCategory } from '@/stores/settingsModal';
export { settingsModal$ } from '@/stores/settingsModal';

interface SettingsModalProps {
  children?: React.ReactNode;
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
  {
    id: 'about' as const,
    label: 'About',
    icon: Info,
    description: 'Version info and links',
  },
];

export const SettingsModal = forwardRef<HTMLButtonElement, SettingsModalProps>(
  ({ children }, _ref) => {
    const { settings, updateSettings, resetSettings } = useSettings();
    const { theme, setTheme } = useTheme();
    const [open, setOpen] = useState(false);
    const [activeCategory, setActiveCategory] = useState<SettingsCategory>('appearance');
    const [serverVersion, setServerVersion] = useState<string | null>(null);

    useEffect(() => {
      if (activeCategory !== 'about') return;
      const client = getPrimaryClient();
      if (!client) return;
      client
        .getServerInfo()
        .then((info) => setServerVersion(info.version ?? null))
        .catch(() => setServerVersion(null));
    }, [activeCategory]);

    // Allow external code to open the modal to a specific category
    const externalRequest = use$(settingsModal$);
    useEffect(() => {
      if (externalRequest.open) {
        setActiveCategory(externalRequest.category);
        setOpen(true);
        settingsModal$.open.set(false);
      }
    }, [externalRequest.open, externalRequest.category]);

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
                    Code blocks open by default
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    Whether code blocks are expanded when first shown
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
                        setOpen(false);
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
                        setOpen(false);
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
      <Dialog open={open} onOpenChange={setOpen}>
        {children !== undefined && <DialogTrigger asChild>{children}</DialogTrigger>}
        <DialogContent className="flex max-h-[90vh] w-[calc(100vw-2rem)] flex-col overflow-hidden p-0 sm:max-h-[80vh] sm:max-w-4xl">
          <DialogHeader className="border-b px-6 py-3">
            <DialogTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Settings
            </DialogTitle>
            <DialogDescription>Customize your gptme experience</DialogDescription>
          </DialogHeader>

          <div className="flex min-h-0 min-w-0 flex-1 flex-col sm:flex-row">
            {/* Mobile: horizontal tab strip */}
            <div className="flex overflow-x-auto border-b bg-muted/20 p-2 sm:hidden">
              {categories.map((category) => {
                const Icon = category.icon;
                const isActive = activeCategory === category.id;
                return (
                  <button
                    key={category.id}
                    onClick={() => setActiveCategory(category.id)}
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
                      onClick={() => setActiveCategory(category.id)}
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
        </DialogContent>
      </Dialog>
    );
  }
);

SettingsModal.displayName = 'SettingsModal';
