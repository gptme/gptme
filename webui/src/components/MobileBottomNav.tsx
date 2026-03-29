import { MessageSquare, History, Kanban, Bot, FolderOpen } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';
import type { FC } from 'react';

const navItems = [
  { id: 'chat', label: 'Chat', icon: MessageSquare, path: '/chat' },
  { id: 'history', label: 'History', icon: History, path: '/history' },
  { id: 'tasks', label: 'Tasks', icon: Kanban, path: '/tasks' },
  { id: 'agents', label: 'Agents', icon: Bot, path: '/agents' },
  { id: 'workspaces', label: 'Workspaces', icon: FolderOpen, path: '/workspaces' },
] as const;

export const MobileBottomNav: FC = () => {
  const location = useLocation();

  return (
    <nav className="flex h-12 items-center justify-around border-t bg-background md:hidden">
      {navItems.map((item) => {
        const Icon = item.icon;
        // Workspaces tab must also activate on /workspace/:id (singular) detail pages
        const isActive =
          item.id === 'workspaces'
            ? location.pathname.startsWith('/workspace')
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
