import {
  MessageSquare,
  Kanban,
  History,
  PanelLeftOpen,
  PanelLeftClose,
  Settings,
  Bot,
  FolderOpen,
  Search,
  Layers,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useNavigate, useLocation } from 'react-router-dom';
import { toggleLeftSidebarCollapsed, leftSidebarCollapsed$ } from '@/stores/sidebar';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import { SettingsModal } from './SettingsModal';
import type { Task } from '@/types/task';
import type { FC } from 'react';
import { use$ } from '@legendapp/state/react';

interface Props {
  tasks: Task[];
}

export const SidebarIcons: FC<Props> = ({ tasks }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const isCollapsed = use$(leftSidebarCollapsed$);

  // Navigation state
  const currentSection = location.pathname.startsWith('/tasks')
    ? 'tasks'
    : location.pathname.startsWith('/history')
      ? 'history'
      : location.pathname.startsWith('/agents')
        ? 'agents'
        : location.pathname.startsWith('/workspaces')
          ? 'workspaces'
          : location.pathname.startsWith('/external-sessions')
            ? 'external-sessions'
            : 'chat';

  const handleNavigateToSection = (
    section: 'chat' | 'tasks' | 'history' | 'agents' | 'workspaces' | 'external-sessions'
  ) => {
    navigate(`/${section === 'chat' ? 'chat' : section}`);
  };

  const activeTasks = tasks.filter((t) => t.status === 'active' && !t.archived);

  return (
    <div className="hidden h-full w-11 flex-col border-r bg-background md:flex">
      {/* Navigation Icons */}
      <div className="flex-shrink-0 space-y-2 p-1">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={currentSection === 'chat' ? 'secondary' : 'ghost'}
                size="icon"
                className="h-8 w-8"
                onClick={() => handleNavigateToSection('chat')}
              >
                <MessageSquare className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Chat</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={currentSection === 'agents' ? 'secondary' : 'ghost'}
                size="icon"
                className="h-8 w-8"
                onClick={() => handleNavigateToSection('agents')}
              >
                <Bot className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Agents</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={currentSection === 'workspaces' ? 'secondary' : 'ghost'}
                size="icon"
                className="h-8 w-8"
                onClick={() => handleNavigateToSection('workspaces')}
              >
                <FolderOpen className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Workspaces</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={currentSection === 'tasks' ? 'secondary' : 'ghost'}
                size="icon"
                className="relative h-8 w-8"
                onClick={() => handleNavigateToSection('tasks')}
              >
                <Kanban className="h-4 w-4" />
                {activeTasks.length > 0 && (
                  <div className="absolute -right-1 -top-1 flex h-3 w-3 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
                    {activeTasks.length}
                  </div>
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Tasks</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={currentSection === 'history' ? 'secondary' : 'ghost'}
                size="icon"
                className="h-8 w-8"
                onClick={() => handleNavigateToSection('history')}
              >
                <History className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">History</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={currentSection === 'external-sessions' ? 'secondary' : 'ghost'}
                size="icon"
                className="h-8 w-8"
                onClick={() => handleNavigateToSection('external-sessions')}
              >
                <Layers className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">External Sessions</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => commandPaletteOpen$.set(true)}
              >
                <Search className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Search (⌘K)</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Settings */}
      <div className="flex-shrink-0 p-1">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <SettingsModal>
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Open settings">
                  <Settings className="h-4 w-4" />
                </Button>
              </SettingsModal>
            </TooltipTrigger>
            <TooltipContent side="right">Settings</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Toggle Button */}
      <div className="flex-shrink-0 p-1">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={toggleLeftSidebarCollapsed}
                data-testid="toggle-conversations-sidebar"
              >
                {isCollapsed ? (
                  <PanelLeftOpen className="h-4 w-4" />
                ) : (
                  <PanelLeftClose className="h-4 w-4" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </div>
  );
};
