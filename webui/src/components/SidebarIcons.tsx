import { MessageSquare, Kanban, PanelLeftOpen, PanelLeftClose } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useNavigate, useLocation } from 'react-router-dom';
import { toggleLeftSidebarCollapsed, leftSidebarCollapsed$ } from '@/stores/sidebar';
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
  const currentSection = location.pathname.startsWith('/tasks') ? 'tasks' : 'chat';

  const handleNavigateToSection = (section: 'chat' | 'tasks') => {
    if (section === 'chat') {
      navigate('/chat');
    } else {
      navigate('/tasks');
    }
  };

  const activeTasks = tasks.filter((t) => t.status === 'active' && !t.archived);

  return (
    <div className="flex h-full w-11 flex-col border-r bg-background">
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
      </div>

      {/* Spacer */}
      <div className="flex-1" />

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
