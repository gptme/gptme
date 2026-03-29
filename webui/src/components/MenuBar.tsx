import { ServerSelector } from './ServerSelector';
import { Button } from './ui/button';
import { User, Search, Menu } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Link } from 'react-router-dom';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import { leftSidebarVisible$, toggleLeftSidebar } from '@/stores/sidebar';
import { use$ } from '@legendapp/state/react';

import type { FC } from 'react';

export const MenuBar: FC = () => {
  const leftSidebarOpen = use$(leftSidebarVisible$);

  return (
    <div className="flex h-9 items-center justify-between border-b px-2 sm:px-4">
      <div className="flex items-center gap-1 sm:gap-4">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 md:hidden"
          onClick={() => toggleLeftSidebar()}
          aria-label={leftSidebarOpen ? 'Close navigation' : 'Open navigation'}
        >
          <Menu className="h-4 w-4" />
        </Button>
        <Link
          to="/chat"
          className="flex items-center space-x-1 transition-opacity hover:opacity-80 sm:space-x-2"
        >
          <img src="/logo.png" alt="gptme logo" className="w-4" />
          <span className="font-mono text-sm font-semibold sm:text-base">gptme</span>
        </Link>
      </div>

      <div className="flex items-center gap-1 sm:gap-4">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="hidden h-7 gap-1.5 px-2 text-xs text-muted-foreground sm:flex"
                onClick={() => commandPaletteOpen$.set(true)}
              >
                <Search className="h-3.5 w-3.5" />
                <span>Search</span>
                <kbd className="pointer-events-none rounded border bg-muted px-1 font-mono text-[10px]">
                  {navigator.platform?.includes('Mac') ? '⌘' : 'Ctrl+'}K
                </kbd>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Search conversations and commands</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 sm:hidden"
                onClick={() => commandPaletteOpen$.set(true)}
                aria-label="Search"
              >
                <Search className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Search</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <ServerSelector />
        {import.meta.env.VITE_EMBEDDED_MODE === 'true' && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Link to="/account">
                  <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Dashboard">
                    <User className="h-4 w-4" />
                  </Button>
                </Link>
              </TooltipTrigger>
              <TooltipContent side="bottom">Dashboard</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
    </div>
  );
};
