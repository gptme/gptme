import { MessageSquare, History, Kanban, Bot, FolderOpen } from 'lucide-react';
import { useNavigate, useLocation } from 'react-router-dom';
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
  const navigate = useNavigate();
  const location = useLocation();

  const currentSection = location.pathname.startsWith('/tasks')
    ? 'tasks'
    : location.pathname.startsWith('/history')
      ? 'history'
      : location.pathname.startsWith('/agents')
        ? 'agents'
        : location.pathname.startsWith('/workspaces') || location.pathname.startsWith('/workspace')
          ? 'workspaces'
          : 'chat';

  return (
    <nav className="flex h-12 items-center justify-around border-t bg-background md:hidden">
      {navItems.map((item) => {
        const Icon = item.icon;
        const isActive = currentSection === item.id;

        return (
          <button
            key={item.id}
            onClick={() => navigate(item.path)}
            className={cn(
              'flex flex-1 flex-col items-center justify-center gap-0.5 py-1 text-muted-foreground transition-colors',
              isActive && 'text-foreground'
            )}
            aria-label={item.label}
          >
            <Icon className="h-4 w-4" />
            <span className="text-[10px] leading-none">{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
};
