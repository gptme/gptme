import { MessageSquare, History, Kanban, Bot, FolderOpen, Search } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import type { FC } from 'react';

const navItems = [
  { id: 'chat', label: 'Chat', icon: MessageSquare, path: '/chat' },
  { id: 'history', label: 'History', icon: History, path: '/history' },
  { id: 'search', label: 'Search', icon: Search, path: '' }, // path unused; opens command palette
  { id: 'tasks', label: 'Tasks', icon: Kanban, path: '/tasks' },
  { id: 'agents', label: 'Agents', icon: Bot, path: '/agents' },
  { id: 'workspaces', label: 'Workspaces', icon: FolderOpen, path: '/workspaces' },
] as const;

export const MobileBottomNav: FC = () => {
  const location = useLocation();

  return (
    <nav
      className="flex items-center justify-around border-t bg-background md:hidden"
      style={{
        minHeight: '3rem',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
      }}
    >
      {navItems.map((item) => {
        const Icon = item.icon;

        // Search opens the command palette instead of navigating
        if (item.id === 'search') {
          return (
            <button
              key={item.id}
              type="button"
              className="flex flex-1 flex-col items-center justify-center gap-0.5 py-1 text-muted-foreground transition-colors"
              onClick={() => commandPaletteOpen$.set(true)}
              aria-label={item.label}
            >
              <Icon className="h-4 w-4" />
              <span className="text-[10px] leading-none">{item.label}</span>
            </button>
          );
        }

        // Workspaces tab must also activate on /workspace/:id (singular) detail pages.
        // Chat tab must also activate on root path '/' which renders the same Index component.
        const isActive =
          item.id === 'workspaces'
            ? location.pathname.startsWith('/workspace')
            : item.id === 'chat'
              ? location.pathname === '/' || location.pathname.startsWith('/chat')
              : location.pathname.startsWith(item.path);

        return (
          <NavLink
            key={item.id}
            to={item.path}
            className={cn(
              'flex flex-1 flex-col items-center justify-center gap-0.5 py-1 text-muted-foreground transition-colors',
              isActive && 'text-foreground'
            )}
            aria-label={item.label}
          >
            <Icon className="h-4 w-4" />
            <span className="text-[10px] leading-none">{item.label}</span>
          </NavLink>
        );
      })}
    </nav>
  );
};
