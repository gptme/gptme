import { ConnectionButton } from './ConnectionButton';
import { SettingsModal } from './SettingsModal';
import { Button } from './ui/button';
import { PanelRightClose, PanelRightOpen, User } from 'lucide-react';
import { rightSidebarVisible$, toggleRightSidebar } from '@/stores/sidebar';
import { use$ } from '@legendapp/state/react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Link } from 'react-router-dom';

import type { FC } from 'react';

interface MenuBarProps {
  showRightSidebar?: boolean;
}

export const MenuBar: FC<MenuBarProps> = ({ showRightSidebar = false }) => {
  const rightVisible = use$(rightSidebarVisible$);

  return (
    <div className="flex h-9 items-center justify-between border-b px-2 sm:px-4">
      <div className="flex items-center space-x-2 sm:space-x-4">
        <Link
          to="/chat"
          className="flex items-center space-x-1 transition-opacity hover:opacity-80 sm:space-x-2"
        >
          <img src="/logo.png" alt="gptme logo" className="w-4" />
          <span className="font-mono text-sm font-semibold sm:text-base">gptme</span>
        </Link>
      </div>

      <div className="flex items-center gap-1 sm:gap-4">
        <div className="flex items-center gap-4">
          <ConnectionButton />
          {import.meta.env.VITE_EMBEDDED_MODE === 'true' && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Link to="/account">
                    <Button variant="ghost" size="icon" className="h-8 w-8">
                      <User className="h-4 w-4" />
                    </Button>
                  </Link>
                </TooltipTrigger>
                <TooltipContent side="bottom">Dashboard</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <SettingsModal />
              </TooltipTrigger>
              <TooltipContent side="bottom">Settings</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        {showRightSidebar && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon" onClick={toggleRightSidebar}>
                  {rightVisible ? (
                    <PanelRightClose className="h-4 w-4" />
                  ) : (
                    <PanelRightOpen className="h-4 w-4" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {rightVisible ? 'Hide sidebar' : 'Show sidebar'}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
    </div>
  );
};
