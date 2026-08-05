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
        {/* Full-screen on mobile; fixed (not content-sized) height on desktop so the
            dialog doesn't resize when switching tabs or expanding sections. */}
        <DialogContent
          className="flex h-dvh w-screen max-w-none flex-col overflow-hidden border-0 p-0 sm:h-[80vh] sm:w-full sm:max-w-4xl sm:border"
          style={{
            paddingTop: 'env(safe-area-inset-top, 0px)',
            paddingBottom: 'env(safe-area-inset-bottom, 0px)',
            paddingLeft: 'env(safe-area-inset-left, 0px)',
            paddingRight: 'env(safe-area-inset-right, 0px)',
          }}
        >
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
