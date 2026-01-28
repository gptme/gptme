import {
  ChevronDown,
  ChevronRight,
  UserRoundPlusIcon,
  PenSquare,
  Plus,
  Filter,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ConversationList } from './ConversationList';
import { AgentsList } from './AgentsList';
import { WorkspaceList } from './WorkspaceList';
import CreateAgentDialog, { type CreateAgentRequest } from './CreateAgentDialog';
import { useApi } from '@/contexts/ApiContext';
import { useLocation, useNavigate } from 'react-router-dom';
import type { ConversationSummary } from '@/types/conversation';
import type { Task } from '@/types/task';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { selectedWorkspace$, selectedAgent$ } from '@/stores/sidebar';
import { Card, CardContent } from '@/components/ui/card';
import { Clock, CheckCircle, XCircle, RefreshCw, GitBranch } from 'lucide-react';

import type { FC } from 'react';
import { use$ } from '@legendapp/state/react';
import { type Observable } from '@legendapp/state';
import { useState, useMemo } from 'react';

// Simplified task display component for sidebar
const TaskListItem: FC<{ task: Task; isSelected: boolean; onClick: () => void }> = ({
  task,
  isSelected,
  onClick,
}) => {
  const getStatusIcon = (status: Task['status']) => {
    switch (status) {
      case 'pending':
        return <Clock className="h-3 w-3 text-yellow-500" />;
      case 'active':
        return <RefreshCw className="h-3 w-3 animate-spin text-blue-500" />;
      case 'completed':
        return <CheckCircle className="h-3 w-3 text-green-500" />;
      case 'failed':
        return <XCircle className="h-3 w-3 text-red-500" />;
    }
  };

  return (
    <div
      className={`mb-1 cursor-pointer rounded-md p-2 text-sm transition-colors hover:bg-accent/50 ${
        isSelected ? 'bg-accent ring-1 ring-primary' : ''
      }`}
      onClick={onClick}
    >
      <div className="flex items-start gap-2">
        {getStatusIcon(task.status)}
        <div className="flex-1 overflow-hidden">
          <p className="line-clamp-2 text-xs font-medium">{task.content}</p>
          <p className="text-xs text-muted-foreground">
            {new Date(task.created_at).toLocaleDateString()}
          </p>
        </div>
      </div>
    </div>
  );
};

interface Props {
  // Conversation props
  conversations: ConversationSummary[];
  selectedConversationId$: Observable<string | null>;
  onSelectConversation: (id: string) => void;
  conversationsLoading?: boolean;
  conversationsFetching?: boolean;
  conversationsError?: boolean;
  conversationsErrorObj?: Error;
  onConversationsRetry?: () => void;
  fetchNextPage: () => void;
  hasNextPage?: boolean;

  // Task props
  tasks: Task[];
  selectedTaskId?: string;
  onSelectTask: (task: Task) => void;
  onCreateTask: () => void;
  tasksLoading?: boolean;
  tasksError?: boolean;
  onTasksRetry?: () => void;
}

export const UnifiedSidebar: FC<Props> = ({
  conversations,
  selectedConversationId$,
  onSelectConversation,
  conversationsLoading = false,
  conversationsFetching = false,
  conversationsError = false,
  conversationsErrorObj,
  onConversationsRetry,
  fetchNextPage,
  hasNextPage = false,
  tasks,
  selectedTaskId,
  onSelectTask,
  onCreateTask,
  tasksLoading = false,
  tasksError = false,
  onTasksRetry,
}) => {
  const { createAgent } = useApi();
  const selectedWorkspace = use$(selectedWorkspace$);
  const selectedAgent = use$(selectedAgent$);
  const location = useLocation();
  const navigate = useNavigate();

  // Navigation state
  const currentSection = location.pathname.startsWith('/tasks') ? 'tasks' : 'chat';

  // Collapsible state
  const [agentsCollapsed, setAgentsCollapsed] = useState(false);
  const [workspacesCollapsed, setWorkspacesCollapsed] = useState(true);
  const [showCreateAgentDialog, setShowCreateAgentDialog] = useState(false);

  // Filter state for tasks
  const [selectedTargetTypes, setSelectedTargetTypes] = useState<Set<string>>(new Set(['all']));
  const [showFilters, setShowFilters] = useState(false);

  // Filter conversations based on selected workspace and agent
  const filteredConversations = useMemo(() => {
    let filtered = conversations;

    if (selectedWorkspace) {
      filtered = filtered.filter((conv) => conv.workspace === selectedWorkspace);
    }

    if (selectedAgent) {
      filtered = filtered.filter((conv) => conv.agent_name === selectedAgent.name);
    }

    return filtered;
  }, [conversations, selectedWorkspace, selectedAgent]);

  // Filter and sort tasks
  const sortedTasks = useMemo(() => {
    // Filter by target type
    let filtered = tasks;
    if (!selectedTargetTypes.has('all')) {
      filtered = tasks.filter((task) => selectedTargetTypes.has(task.target_type));
    }

    // Sort by status
    return [...filtered].sort((a, b) => {
      const statusOrder = { active: 0, pending: 1, failed: 2, completed: 3 };
      return statusOrder[a.status] - statusOrder[b.status];
    });
  }, [tasks, selectedTargetTypes]);

  const handleAgentCreated = async (agentData: CreateAgentRequest) => {
    try {
      return await createAgent(agentData);
    } catch (error) {
      console.error('Failed to create agent:', error);
      throw error;
    }
  };

  const handleNewConversation = () => {
    navigate('/chat');
  };

  // Content for the selected section only (navigation is handled by SidebarIcons)
  return (
    <div className="flex h-full flex-col">
      {/* Chats Section Header */}
      {currentSection === 'chat' && (
        <div className="flex items-center gap-2 bg-background p-2">
          <span className="ml-1 font-medium">Chats</span>
          <div className="flex-1" />
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={handleNewConversation}>
            <PenSquare className="h-4 w-4" />
          </Button>
        </div>
      )}
      {/* Tasks Section Header */}
      {currentSection === 'tasks' && (
        <>
          <div className="flex items-center gap-2 bg-background p-2">
            <span className="ml-1 font-medium">Tasks</span>
            <div className="flex-1" />
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => setShowFilters(!showFilters)}
            >
              <Filter className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onCreateTask}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>

          {/* Task Filters */}
          {showFilters && (
            <div className="border-b bg-muted/30 p-2">
              <div className="flex flex-wrap gap-1">
                {(['all', 'pr', 'email', 'tweet', 'stdout'] as const).map((type) => {
                  const isSelected = selectedTargetTypes.has(type);
                  return (
                    <Button
                      key={type}
                      variant={isSelected ? 'default' : 'outline'}
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => {
                        const newTypes = new Set(selectedTargetTypes);
                        if (type === 'all') {
                          setSelectedTargetTypes(new Set(['all']));
                        } else {
                          newTypes.delete('all');
                          if (isSelected) {
                            newTypes.delete(type);
                            if (newTypes.size === 0) {
                              setSelectedTargetTypes(new Set(['all']));
                            } else {
                              setSelectedTargetTypes(newTypes);
                            }
                          } else {
                            newTypes.add(type);
                            setSelectedTargetTypes(newTypes);
                          }
                        }
                      }}
                    >
                      {type === 'all'
                        ? 'All'
                        : type === 'pr'
                          ? 'PR'
                          : type.charAt(0).toUpperCase() + type.slice(1)}
                    </Button>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      {/* Content Lists - fills available space */}
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          {/* Conversations Section */}
          {currentSection === 'chat' && (
            <ConversationList
              conversations={filteredConversations}
              selectedId$={selectedConversationId$}
              onSelect={onSelectConversation}
              isLoading={conversationsLoading}
              isFetching={conversationsFetching}
              isError={conversationsError}
              error={conversationsErrorObj}
              onRetry={onConversationsRetry}
              fetchNextPage={fetchNextPage}
              hasNextPage={hasNextPage}
            />
          )}

          {/* Tasks Section */}
          {currentSection === 'tasks' && (
            <div>
              <div className="p-2">
                {tasksError && onTasksRetry && (
                  <Card className="mb-4 border-red-200 bg-red-50">
                    <CardContent className="p-3">
                      <p className="text-sm text-red-700">Failed to load tasks</p>
                      <Button size="sm" variant="outline" onClick={onTasksRetry} className="mt-2">
                        Retry
                      </Button>
                    </CardContent>
                  </Card>
                )}

                {!tasksLoading && !tasksError && tasks.length === 0 && (
                  <div className="py-8 text-center">
                    <GitBranch className="mx-auto mb-2 h-8 w-8 text-muted-foreground opacity-50" />
                    <p className="text-sm text-muted-foreground">No tasks yet</p>
                  </div>
                )}

                {sortedTasks.slice(0, 20).map((task) => (
                  <TaskListItem
                    key={task.id}
                    task={task}
                    isSelected={selectedTaskId === task.id}
                    onClick={() => onSelectTask(task)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Bottom Sections - Agents & Workspaces */}
        <div className="flex-shrink-0">
          {/* Agents Section */}
          <Collapsible open={!agentsCollapsed} onOpenChange={(open) => setAgentsCollapsed(!open)}>
            <div className="flex h-12 w-full shrink-0 items-center justify-between border-t bg-background px-4">
              <CollapsibleTrigger className="-mx-2 flex flex-1 items-center space-x-2 rounded-md px-2 py-1 hover:bg-muted/50">
                {agentsCollapsed ? (
                  <ChevronRight className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
                <h3 className="text-sm font-medium">Agents</h3>
              </CollapsibleTrigger>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowCreateAgentDialog(true)}
                      className="h-6 w-6 p-0"
                    >
                      <UserRoundPlusIcon className="h-3 w-3" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Create new agent</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <CollapsibleContent className="overflow-hidden">
              <div className="mb-2" style={{ maxHeight: agentsCollapsed ? 0 : '200px' }}>
                <AgentsList
                  conversations={conversations}
                  handleCreateAgent={() => setShowCreateAgentDialog(true)}
                />
              </div>
            </CollapsibleContent>
          </Collapsible>

          {/* Workspaces Section */}
          <Collapsible
            open={!workspacesCollapsed}
            onOpenChange={(open) => setWorkspacesCollapsed(!open)}
          >
            <CollapsibleTrigger className="flex h-12 w-full shrink-0 items-center justify-between border-t bg-background px-4 hover:bg-muted/50">
              <div className="flex items-center space-x-2">
                {workspacesCollapsed ? (
                  <ChevronRight className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
                <h3 className="text-sm font-medium">Workspaces</h3>
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent className="overflow-hidden">
              <div
                className="overflow-y-auto pb-4"
                style={{ maxHeight: workspacesCollapsed ? 0 : '200px' }}
              >
                <WorkspaceList conversations={conversations} />
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
      </div>

      <CreateAgentDialog
        open={showCreateAgentDialog}
        onOpenChange={setShowCreateAgentDialog}
        onAgentCreated={handleAgentCreated}
      />
    </div>
  );
};
