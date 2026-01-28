import { useState, forwardRef } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import { Settings, Volume2, Palette, Info, FileText, ExternalLink } from 'lucide-react';
import { useSettings } from '@/contexts/SettingsContext';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';

interface SettingsModalProps {
  children?: React.ReactNode;
}

type SettingsCategory = 'appearance' | 'audio' | 'content' | 'about';

const categories = [
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

    const renderCategoryContent = () => {
      switch (activeCategory) {
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
                      href="https://github.com/gptme/gptme-webui"
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
        <DialogTrigger asChild>
          {children || (
            <Button variant="ghost" size="icon">
              <Settings className="h-4 w-4" />
            </Button>
          )}
        </DialogTrigger>
        <DialogContent className="max-h-[80vh] max-w-4xl p-0">
          <DialogHeader className="border-b px-6 py-3">
            <DialogTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Settings
            </DialogTitle>
            <DialogDescription>Customize your gptme experience</DialogDescription>
          </DialogHeader>

          <div className="flex min-h-[500px]">
            {/* Sidebar */}
            <div className="w-64 border-r bg-muted/20 p-4">
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
            <div className="flex-1 overflow-y-auto p-6">{renderCategoryContent()}</div>
          </div>
        </DialogContent>
      </Dialog>
    );
  }
);

SettingsModal.displayName = 'SettingsModal';
