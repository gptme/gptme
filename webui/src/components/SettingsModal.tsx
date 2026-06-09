import { useState, forwardRef, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Settings } from 'lucide-react';
import { use$ } from '@legendapp/state/react';
import { settingsModal$, type SettingsCategory } from '@/stores/settingsModal';
import { SettingsContent } from './SettingsContent';
export type { SettingsCategory } from '@/stores/settingsModal';
export { settingsModal$ } from '@/stores/settingsModal';

interface SettingsModalProps {
  children?: React.ReactNode;
}

export const SettingsModal = forwardRef<HTMLButtonElement, SettingsModalProps>(
  function SettingsModal({ children }, _ref) {
    const [open, setOpen] = useState(false);
    const [activeCategory, setActiveCategory] = useState<SettingsCategory>('appearance');

    // Sync open state with the observable (for external control, e.g. MenuBar search button, WelcomeView)
    const externalRequest = use$(settingsModal$);
    useEffect(() => {
      if (externalRequest.open) {
        setOpen(true);
        if (externalRequest.category) {
          setActiveCategory(externalRequest.category);
        }
        // Reset immediately to prevent auto-reopen on component remount
        // when user navigates away while the modal is open
        settingsModal$.open.set(false);
      }
    }, [externalRequest.open, externalRequest.category]);

    const handleOpenChange = (newOpen: boolean) => {
      setOpen(newOpen);
      if (!newOpen) {
        settingsModal$.open.set(false);
      }
    };

    return (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        {children !== undefined && <DialogTrigger asChild>{children}</DialogTrigger>}
        <DialogContent className="flex max-h-[90vh] w-[calc(100vw-2rem)] flex-col overflow-hidden p-0 sm:max-h-[80vh] sm:max-w-4xl">
          <DialogHeader className="border-b px-6 py-3">
            <DialogTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Settings
            </DialogTitle>
            <DialogDescription>Customize your gptme experience</DialogDescription>
          </DialogHeader>

          <SettingsContent
            activeCategory={activeCategory}
            onCategoryChange={setActiveCategory}
            onClose={() => handleOpenChange(false)}
          />
        </DialogContent>
      </Dialog>
    );
  }
);

SettingsModal.displayName = 'SettingsModal';
