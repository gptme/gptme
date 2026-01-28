import { type FC, useState, useEffect, useRef, useMemo, useCallback } from 'react';
import type { ImperativePanelHandle } from 'react-resizable-panels';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { WelcomeView } from '@/components/WelcomeView';
import { ConversationContent } from '@/components/ConversationContent';
import { TaskDetails } from '@/components/TaskDetails';
import { RightSidebar } from '@/components/RightSidebar';
import { RightSidebarContent } from '@/components/RightSidebarContent';
import { TaskCreationDialog } from '@/components/TaskCreationDialog';
import { SidebarIcons } from '@/components/SidebarIcons';
import { UnifiedSidebar } from '@/components/UnifiedSidebar';
import { setDocumentTitle } from '@/utils/title';
import { useQueryClient } from '@tanstack/react-query';
import { useConversationsInfiniteQuery } from '@/hooks/useConversationsInfiniteQuery';
import { useApi } from '@/contexts/ApiContext';
import { demoConversations, getDemoMessages } from '@/democonversations';
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom';
import { Memo, use$, useObservable, useObserveEffect } from '@legendapp/state/react';
import { Loader2, GitBranch } from 'lucide-react';
import type { ConversationSummary } from '@/types/conversation';
import type { Task, CreateTaskRequest } from '@/types/task';
import {
  initializeConversations,
  selectedConversation$,
  initConversation,
  conversations$,
} from '@/stores/conversations';
import {
  leftSidebarVisible$,
  rightSidebarVisible$,
  rightSidebarActiveTab$,
  leftSidebarCollapsed$,
  setLeftPanelRef,
  setRightPanelRef,
} from '@/stores/sidebar';
import { selectedTask$, useTasksQuery, useTaskQuery, useCreateTaskMutation } from '@/stores/tasks';

interface Props {
  conversationId?: string;
  taskId?: string;
}

const MainLayout: FC<Props> = ({ conversationId, taskId }) => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const stepParam = searchParams.get('step');
  const { api, isConnected$ } = useApi();
  const queryClient = useQueryClient();
  const isConnected = use$(isConnected$);
  const conversation$ = useObservable<ConversationSummary | undefined>(undefined);
  const selectedTaskId = use$(selectedTask$);

  // Determine current section from URL
  const currentSection = location.pathname.startsWith('/tasks') ? 'tasks' : 'chat';

  // Mobile detection
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Task dialog state
  const [showCreateTaskDialog, setShowCreateTaskDialog] = useState(false);

  // Initialize demo conversations and handle selection on mount
  useEffect(() => {
    demoConversations.forEach((conv) => {
      const messages = getDemoMessages(conv.id);
      initConversation(conv.id, {
        id: conv.id,
        name: conv.name,
        log: messages,
        logfile: conv.name,
        branches: {},
        workspace: conv.workspace || '/demo/workspace',
      });
    });

    // Handle initial conversation/task selection
    if (conversationId && selectedConversation$.get() !== conversationId) {
      selectedConversation$.set(conversationId);
    } else if (taskId && selectedTask$.get() !== taskId) {
      selectedTask$.set(taskId);
    } else if (!conversationId && !taskId) {
      // Only clear if they're not already empty
      if (selectedConversation$.get() !== '') {
        selectedConversation$.set('');
      }
      if (selectedTask$.get() !== '') {
        selectedTask$.set('');
      }
    }
  }, [conversationId, taskId]);

  // Sidebar state
  const leftVisible = use$(leftSidebarVisible$);
  const rightVisible = use$(rightSidebarVisible$);
  const rightActiveTab = use$(rightSidebarActiveTab$);
  const currentConversation = conversation$.get();

  // Handle step parameter for auto-generation
  useEffect(() => {
    if (stepParam === 'true' && conversationId && isConnected) {
      console.log(`[MainLayout] Step parameter detected for ${conversationId}`);

      const checkAndStart = () => {
        const conversation = conversations$.get(conversationId);
        if (conversation?.isConnected.get()) {
          console.log(
            `[MainLayout] Conversation ${conversationId} is connected, starting generation`
          );

          api.step(conversationId).catch((error) => {
            console.error('[MainLayout] Failed to start generation:', error);
          });

          const newSearchParams = new URLSearchParams(searchParams);
          newSearchParams.delete('step');
          const queryString = newSearchParams.toString();
          const url = `/chat/${conversationId}${queryString ? `?${queryString}` : ''}`;
          navigate(url, { replace: true });
        } else {
          setTimeout(checkAndStart, 100);
        }
      };

      checkAndStart();
    }
  }, [stepParam, conversationId, isConnected, api, navigate, searchParams]);

  // Fetch conversations from API
  const { data, isError, error, isLoading, isFetching, fetchNextPage, hasNextPage, refetch } =
    useConversationsInfiniteQuery();

  const apiConversations = useMemo(() => {
    return (
      data?.pages.flatMap(
        (page: { conversations: ConversationSummary[]; nextCursor: number | undefined }) =>
          page.conversations
      ) ?? []
    );
  }, [data]);

  // Fetch tasks
  const {
    data: tasks = [],
    isLoading: tasksLoading,
    error: tasksError,
    refetch: refetchTasks,
  } = useTasksQuery();
  const { data: selectedTask } = useTaskQuery(selectedTaskId || null);
  const createTaskMutation = useCreateTaskMutation();

  if (isError) {
    console.error('Conversation query error:', error);
  }

  const demoItems: ConversationSummary[] = useMemo(() => demoConversations, []);

  const apiItems: ConversationSummary[] = useMemo(() => {
    if (!isConnected) return [];
    return apiConversations;
  }, [isConnected, apiConversations]);

  useEffect(() => {
    if (isConnected && apiConversations.length) {
      console.log('[MainLayout] Initializing API conversations');
      void initializeConversations(
        api,
        apiConversations.map((c) => c.id),
        10
      );
    }
  }, [isConnected, apiConversations, api]);

  // Reactive computation for store conversations using Legend State
  const storeConversations$ = useObservable(() => {
    const storeConvs = Array.from(conversations$.get().entries())
      .filter(([id, state]) => {
        // Only include non-demo conversations that have actual data
        const isDemoConv = demoItems.some((demo) => demo.id === id);
        return !isDemoConv && state.data.log && state.data.log.length > 0;
      })
      .map(
        ([id, state]): ConversationSummary => ({
          id,
          name: state.data.name || 'New conversation',
          modified: state.lastMessage
            ? new Date(state.lastMessage.timestamp || Date.now()).getTime()
            : Date.now(),
          messages: state.data.log?.length || 0,
          workspace: state.data.workspace || '.',
          readonly: false,
        })
      );
    return storeConvs;
  });

  const allConversations: ConversationSummary[] = useMemo(() => {
    const storeConvs = storeConversations$.get();

    // Combine demo, API, and store conversations, deduplicating by ID
    const conversationMap = new Map<string, ConversationSummary>();

    // Add in order of preference: API items (most up-to-date), store items, demo items
    [...apiItems, ...storeConvs, ...demoItems].forEach((conv) => {
      if (!conversationMap.has(conv.id)) {
        conversationMap.set(conv.id, conv);
      }
    });

    return Array.from(conversationMap.values());
  }, [demoItems, apiItems, storeConversations$]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      if (id === selectedConversation$.get()) {
        return;
      }
      queryClient.cancelQueries({
        queryKey: ['conversation', selectedConversation$.get()],
      });
      selectedConversation$.set(id);

      const queryString = searchParams.toString();
      const url = `/chat/${id}${queryString ? `?${queryString}` : ''}`;
      navigate(url);
    },
    [queryClient, navigate, searchParams]
  );

  const handleSelectTask = useCallback(
    (task: Task) => {
      selectedTask$.set(task.id);
      navigate(`/tasks/${task.id}`);
    },
    [navigate]
  );

  const handleCreateTask = useCallback(
    async (taskRequest: CreateTaskRequest) => {
      try {
        await createTaskMutation.mutateAsync(taskRequest);
        setShowCreateTaskDialog(false);
        if (selectedTaskId) {
          navigate(`/tasks/${selectedTaskId}`, { replace: true });
        }
      } catch (err) {
        console.error('Error creating task:', err);
      }
    },
    [createTaskMutation, selectedTaskId, navigate]
  );

  // Immediately clear conversation state when no conversationId is provided
  if (!conversationId && !taskId) {
    if (selectedConversation$.get() !== '' || conversation$.get() !== undefined) {
      selectedConversation$.set('');
      selectedTask$.set('');
      conversation$.set(undefined);
    }
  }

  // Update conversation$ when selected conversation changes
  useObserveEffect(selectedConversation$, ({ value: selectedConversation }) => {
    if (selectedConversation) {
      let conversation = allConversations.find((conv) => conv.id === selectedConversation);

      // If not found in allConversations, check the conversations store directly
      if (!conversation) {
        const storeConversation = conversations$.get(selectedConversation)?.get();

        if (storeConversation) {
          // Create conversation summary even if no messages yet - let ConversationContent handle loading
          conversation = {
            id: selectedConversation,
            name: storeConversation.data.name || 'New conversation',
            modified: storeConversation.lastMessage
              ? new Date(storeConversation.lastMessage.timestamp || Date.now()).getTime()
              : Date.now(),
            messages: storeConversation.data.log?.length || 0,
            workspace: storeConversation.data.workspace || '.',
            readonly: false,
          };
        } else {
          // Even if not in store yet, create a minimal conversation to trigger ConversationContent loading
          conversation = {
            id: selectedConversation,
            name: 'Loading...',
            modified: Date.now(),
            messages: 0,
            workspace: '.',
            readonly: false,
          };
        }
      }

      conversation$.set(conversation);
    } else {
      conversation$.set(undefined);
    }
  });

  useEffect(() => {
    const selectedId = selectedConversation$.get();
    if (selectedId) {
      const selectedConversation = allConversations.find((conv) => conv.id === selectedId);
      conversation$.set(selectedConversation);
    } else {
      // Ensure conversation is cleared when no conversation is selected
      conversation$.set(undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allConversations]); // conversation$ is an observable we're setting, not reading

  // Update document title
  useObserveEffect(conversation$, ({ value: conversation }) => {
    if (conversation) {
      setDocumentTitle(conversation.name);
    } else if (selectedTask) {
      setDocumentTitle(`Task: ${selectedTask.content.substring(0, 50)}...`);
    } else {
      setDocumentTitle();
    }
    return () => setDocumentTitle();
  });

  const leftPanelRef = useRef<ImperativePanelHandle>(null);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);

  useEffect(() => {
    if (!isMobile) {
      setLeftPanelRef(leftPanelRef.current);
      setRightPanelRef(rightPanelRef.current);
    }
    return () => {
      setLeftPanelRef(null);
      setRightPanelRef(null);
    };
  }, [isMobile]);

  useEffect(() => {
    if (!isMobile) {
      // Expand sidebar by default
      leftPanelRef.current?.expand();
    }
  }, [isMobile]);

  // Render main content based on current section
  const renderMainContent = () => {
    if (currentSection === 'tasks') {
      if (selectedTask) {
        return <TaskDetails task={selectedTask} />;
      }
      if (selectedTaskId) {
        return (
          <div className="flex h-full flex-1 items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Loader2 className="mx-auto mb-4 h-8 w-8 animate-spin" />
              <p className="mb-2 text-lg">Loading task details...</p>
            </div>
          </div>
        );
      }
      return (
        <div className="flex h-full flex-1 items-center justify-center text-muted-foreground">
          <div className="text-center">
            <GitBranch className="mx-auto mb-4 h-12 w-12 opacity-50" />
            <p className="mb-2 text-lg">No task selected</p>
            <p className="text-sm">Select a task from the sidebar to view details</p>
          </div>
        </div>
      );
    }

    // Chat section
    const conversation = conversation$.get();
    if (conversation) {
      return (
        <div className="h-full overflow-auto">
          <ConversationContent
            conversationId={conversation.id}
            isReadOnly={conversation.readonly}
          />
        </div>
      );
    }

    return (
      <div className="flex h-full flex-1 items-center justify-center p-4">
        <WelcomeView onToggleHistory={() => leftPanelRef.current?.expand()} />
      </div>
    );
  };

  if (isMobile) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {/* Mobile Layout */}
        <Sheet
          open={leftVisible}
          onOpenChange={(open) => {
            leftSidebarVisible$.set(open);
          }}
        >
          <SheetContent side="left" className="flex w-full flex-col p-0 sm:max-w-md">
            <SheetHeader className="flex-shrink-0 border-b p-4">
              <SheetTitle className="text-left text-base font-semibold">Navigation</SheetTitle>
            </SheetHeader>
            <div className="min-h-0 flex-1">
              <UnifiedSidebar
                conversations={allConversations}
                selectedConversationId$={selectedConversation$}
                onSelectConversation={(id) => {
                  handleSelectConversation(id);
                  leftSidebarVisible$.set(false);
                }}
                conversationsLoading={isLoading}
                conversationsFetching={isFetching}
                conversationsError={isError}
                conversationsErrorObj={error as Error}
                onConversationsRetry={() => refetch()}
                fetchNextPage={fetchNextPage}
                hasNextPage={hasNextPage}
                tasks={tasks}
                selectedTaskId={selectedTaskId || undefined}
                onSelectTask={(task) => {
                  handleSelectTask(task);
                  leftSidebarVisible$.set(false);
                }}
                onCreateTask={() => setShowCreateTaskDialog(true)}
                tasksLoading={tasksLoading}
                tasksError={!!tasksError}
                onTasksRetry={() => refetchTasks()}
              />
            </div>
          </SheetContent>
        </Sheet>

        {/* Main Content */}
        <div className="flex-1 overflow-hidden">{renderMainContent()}</div>

        {/* Right Sidebar - Sheet for mobile */}
        {currentSection === 'chat' && (
          <Sheet
            open={rightVisible && !!rightActiveTab && !!currentConversation}
            onOpenChange={(open) => {
              rightSidebarVisible$.set(open);
            }}
          >
            <SheetContent side="right" className="h-full w-full p-0 sm:max-w-md">
              <div className="flex h-full">
                <div className="flex-1">
                  <Memo>
                    {() => {
                      const activeTab = use$(rightSidebarActiveTab$);
                      const conversation = conversation$.get();

                      return activeTab && conversation ? (
                        <RightSidebarContent
                          conversationId={conversation.id}
                          activeTab={activeTab}
                        />
                      ) : (
                        <div className="flex h-full items-center justify-center text-muted-foreground">
                          Select a tab to view content
                        </div>
                      );
                    }}
                  </Memo>
                </div>

                <div className="w-12 flex-shrink-0">
                  <Memo>
                    {() => {
                      const conversation = conversation$.get();
                      return conversation ? (
                        <RightSidebar conversationId={conversation.id} />
                      ) : null;
                    }}
                  </Memo>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        )}

        <TaskCreationDialog
          open={showCreateTaskDialog}
          onOpenChange={setShowCreateTaskDialog}
          onTaskCreated={handleCreateTask}
        />
      </div>
    );
  }

  // Desktop Layout
  return (
    <div className="flex min-h-0 flex-1">
      {/* Fixed icon sidebar - always visible */}
      <SidebarIcons tasks={tasks} />

      <ResizablePanelGroup direction="horizontal" className="h-full">
        <ResizablePanel
          ref={leftPanelRef}
          defaultSize={20}
          minSize={15}
          maxSize={30}
          collapsible
          collapsedSize={0}
          onCollapse={() => {
            leftSidebarVisible$.set(false);
            leftSidebarCollapsed$.set(true);
          }}
          onExpand={() => {
            leftSidebarVisible$.set(true);
            leftSidebarCollapsed$.set(false);
          }}
        >
          <UnifiedSidebar
            conversations={allConversations}
            selectedConversationId$={selectedConversation$}
            onSelectConversation={handleSelectConversation}
            conversationsLoading={isLoading}
            conversationsFetching={isFetching}
            conversationsError={isError}
            conversationsErrorObj={error as Error}
            onConversationsRetry={() => refetch()}
            fetchNextPage={fetchNextPage}
            hasNextPage={hasNextPage}
            tasks={tasks}
            selectedTaskId={selectedTaskId || undefined}
            onSelectTask={handleSelectTask}
            onCreateTask={() => setShowCreateTaskDialog(true)}
            tasksLoading={tasksLoading}
            tasksError={!!tasksError}
            onTasksRetry={() => refetchTasks()}
          />
        </ResizablePanel>

        <ResizableHandle />

        <ResizablePanel defaultSize={60} minSize={30} className="overflow-hidden">
          {renderMainContent()}
        </ResizablePanel>

        {/* Conditional right sidebar for chat */}
        {currentSection === 'chat' && (
          <Memo>
            {() => {
              const rightVisible = use$(rightSidebarVisible$);
              const activeTab = use$(rightSidebarActiveTab$);
              const conversation = conversation$.get();

              return rightVisible && activeTab && conversation ? (
                <>
                  <ResizableHandle />
                  <ResizablePanel
                    ref={rightPanelRef}
                    defaultSize={25}
                    minSize={15}
                    maxSize={40}
                    collapsible
                    collapsedSize={0}
                    onCollapse={() => rightSidebarVisible$.set(false)}
                    onExpand={() => rightSidebarVisible$.set(true)}
                  >
                    <RightSidebarContent conversationId={conversation.id} activeTab={activeTab} />
                  </ResizablePanel>
                </>
              ) : null;
            }}
          </Memo>
        )}

        {/* Fixed navigation icons for chat - always visible */}
        {currentSection === 'chat' && (
          <div className="w-12 flex-shrink-0">
            <Memo>
              {() => {
                const conversation = conversation$.get();
                return conversation ? <RightSidebar conversationId={conversation.id} /> : null;
              }}
            </Memo>
          </div>
        )}
      </ResizablePanelGroup>

      <TaskCreationDialog
        open={showCreateTaskDialog}
        onOpenChange={setShowCreateTaskDialog}
        onTaskCreated={handleCreateTask}
      />
    </div>
  );
};

export default MainLayout;
