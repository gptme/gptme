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
  ChevronRight,
  ChevronLeft,
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
import { useState, useEffect } from 'react';

const NAV_SIDEBAR_KEY = 'nav-sidebar-expanded';

interface NavItem {
  id: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  section: 'chat' | 'tasks' | 'history' | 'agents' | 'workspaces' | 'external-sessions';
}

interface Props {
  tasks: Task[];
}

export const SidebarIcons: FC<Props> = ({ tasks }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const isConversationPanelCollapsed = use$(leftSidebarCollapsed$);

  // User's manual preference for nav sidebar expansion
  const [prefExpanded, setPrefExpanded] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(NAV_SIDEBAR_KEY);
      return stored !== null ? stored === 'true' : true;
    } catch {
      return true;
    }
  });

  // Auto-collapse below lg breakpoint (1024px)
  const [isLargeScreen, setIsLargeScreen] = useState<boolean>(() => window.innerWidth >= 1024);

  useEffect(() => {
    const handleResize = () => setIsLargeScreen(window.innerWidth >= 1024);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Effective state: only expand if user wants it AND screen is large enough
  const isExpanded = prefExpanded && isLargeScreen;

  const toggleNavExpanded = () => {
    const next = !prefExpanded;
    setPrefExpanded(next);
    try {
      localStorage.setItem(NAV_SIDEBAR_KEY, String(next));
    } catch (_e) {
      // localStorage unavailable in some environments
    }
  };

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

  const handleNavigateToSection = (section: NavItem['section']) => {
    navigate(`/${section === 'chat' ? 'chat' : section}`);
  };

  const activeTasks = tasks.filter((t) => t.status === 'active' && !t.archived);

  const navItems: NavItem[] = [
    { id: 'chat', icon: MessageSquare, label: 'Chat', section: 'chat' },
    { id: 'agents', icon: Bot, label: 'Agents', section: 'agents' },
    { id: 'workspaces', icon: FolderOpen, label: 'Workspaces', section: 'workspaces' },
    { id: 'tasks', icon: Kanban, label: 'Tasks', section: 'tasks' },
    { id: 'history', icon: History, label: 'History', section: 'history' },
    { id: 'external-sessions', icon: Layers, label: 'External', section: 'external-sessions' },
  ];

  return (
    <div
      className={`hidden h-full flex-col overflow-hidden border-r bg-background transition-[width] duration-200 ease-in-out md:flex ${
        isExpanded ? 'w-44' : 'w-11'
      }`}
      data-testid="nav-sidebar"
      data-expanded={isExpanded}
    >
      {/* Navigation Items */}
      <div className="flex-shrink-0 space-y-1 p-1">
        {navItems.map(({ id, icon: Icon, label, section }) => {
          const isActive = currentSection === section;
          const badge = id === 'tasks' && activeTasks.length > 0 ? activeTasks.length : null;

          return (
            <TooltipProvider key={id}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={isActive ? 'secondary' : 'ghost'}
                    className="relative h-8 w-full min-w-0 justify-start gap-2 px-2"
                    onClick={() => handleNavigateToSection(section)}
                    aria-label={label}
                  >
                    <Icon className="h-4 w-4 flex-shrink-0" />
                    <span
                      className={`min-w-0 flex-1 truncate text-left text-sm transition-opacity duration-150 ${
                        isExpanded ? 'opacity-100' : 'opacity-0'
                      }`}
                    >
                      {label}
                    </span>
                    {badge !== null && (
                      <div
                        className={`flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-xs text-primary-foreground transition-all duration-150 ${
                          isExpanded
                            ? 'ml-auto opacity-100'
                            : 'absolute -right-1 -top-1 h-3 min-w-3 opacity-100'
                        }`}
                      >
                        {badge}
                      </div>
                    )}
                  </Button>
                </TooltipTrigger>
                {!isExpanded && <TooltipContent side="right">{label}</TooltipContent>}
              </Tooltip>
            </TooltipProvider>
          );
        })}

        {/* Search */}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                className="h-8 w-full min-w-0 justify-start gap-2 px-2"
                onClick={() => commandPaletteOpen$.set(true)}
                aria-label="Search"
              >
                <Search className="h-4 w-4 flex-shrink-0" />
                <span
                  className={`flex min-w-0 flex-1 items-center gap-1 text-sm transition-opacity duration-150 ${
                    isExpanded ? 'opacity-100' : 'opacity-0'
                  }`}
                >
                  <span className="truncate">Search</span>
                  <span className="ml-auto flex-shrink-0 text-xs text-muted-foreground">⌘K</span>
                </span>
              </Button>
            </TooltipTrigger>
            {!isExpanded && <TooltipContent side="right">Search (⌘K)</TooltipContent>}
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
                <Button
                  variant="ghost"
                  className="h-8 w-full min-w-0 justify-start gap-2 px-2"
                  aria-label="Open settings"
                >
                  <Settings className="h-4 w-4 flex-shrink-0" />
                  <span
                    className={`min-w-0 flex-1 truncate text-left text-sm transition-opacity duration-150 ${
                      isExpanded ? 'opacity-100' : 'opacity-0'
                    }`}
                  >
                    Settings
                  </span>
                </Button>
              </SettingsModal>
            </TooltipTrigger>
            {!isExpanded && <TooltipContent side="right">Settings</TooltipContent>}
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Conversation panel toggle */}
      <div className="flex-shrink-0 p-1">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                className="h-8 w-full min-w-0 justify-start gap-2 px-2"
                onClick={toggleLeftSidebarCollapsed}
                data-testid="toggle-conversations-sidebar"
                aria-label={isConversationPanelCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              >
                {isConversationPanelCollapsed ? (
                  <PanelLeftOpen className="h-4 w-4 flex-shrink-0" />
                ) : (
                  <PanelLeftClose className="h-4 w-4 flex-shrink-0" />
                )}
                <span
                  className={`min-w-0 flex-1 truncate text-left text-sm transition-opacity duration-150 ${
                    isExpanded ? 'opacity-100' : 'opacity-0'
                  }`}
                >
                  {isConversationPanelCollapsed ? 'Show Chats' : 'Hide Chats'}
                </span>
              </Button>
            </TooltipTrigger>
            {!isExpanded && (
              <TooltipContent side="right">
                {isConversationPanelCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              </TooltipContent>
            )}
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Nav sidebar expand/collapse toggle */}
      <div className="flex-shrink-0 p-1">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                className="h-8 w-full min-w-0 justify-start gap-2 px-2"
                onClick={toggleNavExpanded}
                aria-label={isExpanded ? 'Collapse navigation' : 'Expand navigation'}
                data-testid="toggle-nav-sidebar"
              >
                {isExpanded ? (
                  <ChevronLeft className="h-4 w-4 flex-shrink-0" />
                ) : (
                  <ChevronRight className="h-4 w-4 flex-shrink-0" />
                )}
                <span
                  className={`min-w-0 flex-1 truncate text-left text-sm transition-opacity duration-150 ${
                    isExpanded ? 'opacity-100' : 'opacity-0'
                  }`}
                >
                  Collapse
                </span>
              </Button>
            </TooltipTrigger>
            {!isExpanded && <TooltipContent side="right">Expand navigation</TooltipContent>}
          </Tooltip>
        </TooltipProvider>
      </div>
    </div>
  );
};
