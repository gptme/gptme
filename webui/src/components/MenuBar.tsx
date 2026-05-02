import { ServerSelector } from './ServerSelector';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { ChevronDown, Menu, Search, User } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useEmbeddedContext } from '@/contexts/EmbeddedContext';
import type { EmbeddedMenuItem } from '@/lib/embeddedContext';
import { Link } from 'react-router-dom';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import { leftSidebarVisible$, toggleLeftSidebar } from '@/stores/sidebar';
import { use$ } from '@legendapp/state/react';

import { Fragment, type FC } from 'react';

function groupEmbeddedMenuItems(menuItems: EmbeddedMenuItem[]) {
  return menuItems.reduce<{ section?: string; items: EmbeddedMenuItem[] }[]>((groups, item) => {
    const section = item.section?.trim() || undefined;
    const lastGroup = groups.at(-1);

    if (lastGroup && lastGroup.section === section) {
      lastGroup.items.push(item);
      return groups;
    }

    groups.push({ section, items: [item] });
    return groups;
  }, []);
}

export const MenuBar: FC = () => {
  const leftSidebarOpen = use$(leftSidebarVisible$);
  const { menuItems, sendAction } = useEmbeddedContext();
  const isEmbedded = import.meta.env.VITE_EMBEDDED_MODE === 'true';
  const embeddedMenuGroups = groupEmbeddedMenuItems(menuItems);
  const hasEmbeddedMenu = isEmbedded && embeddedMenuGroups.length > 0;

  return (
    <div
      className="flex items-center justify-between border-b px-2 sm:px-4"
      style={{
        minHeight: '2.25rem',
        paddingTop: 'env(safe-area-inset-top, 0px)',
      }}
    >
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
        {hasEmbeddedMenu ? (
          <DropdownMenu>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 gap-1 px-2"
                      aria-label="Account menu"
                    >
                      <User className="h-4 w-4" />
                      <ChevronDown className="h-3 w-3" />
                    </Button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent side="bottom">Account</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <DropdownMenuContent align="end" className="w-56">
              {embeddedMenuGroups.map((group, groupIndex) => (
                <Fragment key={group.section ?? `group-${groupIndex}`}>
                  {groupIndex > 0 && <DropdownMenuSeparator />}
                  {group.section && <DropdownMenuLabel>{group.section}</DropdownMenuLabel>}
                  {group.items.map((item) =>
                    item.kind === 'link' ? (
                      <DropdownMenuItem key={item.id} asChild>
                        <a href={item.href} target="_top" rel="noreferrer">
                          {item.label}
                        </a>
                      </DropdownMenuItem>
                    ) : (
                      <DropdownMenuItem
                        key={item.id}
                        className={
                          item.destructive ? 'text-destructive focus:text-destructive' : undefined
                        }
                        onSelect={() => sendAction(item.action, item.id)}
                      >
                        {item.label}
                      </DropdownMenuItem>
                    )
                  )}
                </Fragment>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          isEmbedded && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <a href="/account" target="_top" rel="noreferrer">
                    <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Dashboard">
                      <User className="h-4 w-4" />
                    </Button>
                  </a>
                </TooltipTrigger>
                <TooltipContent side="bottom">Dashboard</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )
        )}
      </div>
    </div>
  );
};
