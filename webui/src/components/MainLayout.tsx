import { type FC, useState, useEffect, useRef, useMemo, useCallback } from 'react';
import type { ImperativePanelHandle } from 'react-resizable-panels';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { WelcomeView } from '@/components/WelcomeView';
import { ConversationContent } from '@/components/ConversationContent';
import { ExternalSessionDetail } from '@/components/ExternalSessionDetail';
import { SplitConversationView } from '@/components/SplitConversationView';
import { TaskDetails } from '@/components/TaskDetails';
import { RightSidebar } from '@/components/RightSidebar';
import { RightSidebarContent } from '@/components/RightSidebarContent';
import { TaskCreationDialog } from '@/components/TaskCreationDialog';
import { SidebarIcons } from '@/components/SidebarIcons';
import { SettingsModal } from '@/components/SettingsModal';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { UnifiedSidebar } from '@/components/UnifiedSidebar';
import { AgentsView } from '@/components/AgentsView';
import { WorkspacesView } from '@/components/WorkspacesView';
import { useToast } from '@/components/ui/use-toast';
import { setDocumentTitle } from '@/utils/title';
import { toastStepStartError } from '@/utils/stepErrorHandling';
import { chatRoute } from '@/utils/routes';
import { useQueryClient } from '@tanstack/react-query';
import { useConversationsInfiniteQuery } from '@/hooks/useConversationsInfiniteQuery';
import { useSecondaryServerConversations } from '@/hooks/useMultiServerConversations';
import { useApi } from '@/contexts/ApiContext';
import { serverRegistry$ } from '@/stores/servers';
import { demoConversations, getDemoMessages } from '@/democonversations';
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom';
import { Memo, use$, useObservable, useObserveEffect } from '@legendapp/state/react';
import { Loader2, GitBranch, Columns2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { ConversationSummary } from '@/types/conversation';
import type { Task, CreateTaskRequest } from '@/types/task';
import { selectedConversation$, initConversation, conversations$ } from '@/stores/conversations';
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
  const serverParam = searchParams.get('server');
  const splitParam = searchParams.get('split');
  const externalParam = searchParams.get('external');
  const splitIds = useMemo((): [string, string] | null => {
    if (!splitParam) return null;
    const ids = splitParam.split(',').filter(Boolean).slice(0, 2);
    return ids.length === 2 ? (ids as [string, string]) : null;
  }, [splitParam]);
  const { api, isConnected$ } = useApi();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const isConnected = use$(isConnected$);
  const conversation$ = useObservable<ConversationSummary | undefined>(undefined);
  const selectedTaskId = use$(selectedTask$);

  // Determine current section from URL
  const currentSection = location.pathname.startsWith('/tasks')
    ? 'tasks'
    : location.pathname.startsWith('/agents')
      ? 'agents'
      : location.pathname.startsWith('/workspaces')
        ? 'workspaces'
        : 'chat';

  // Mobile detection
  const [isMobile, setIsMobile] = useState(false);
  const prevMobileRef = useRef(false);

  useEffect(() => {
    const checkMobile = () => {
      const mobile = window.innerWidth < 768;
      // Close left sidebar when entering mobile mode to prevent Sheet auto-opening
      if (mobile && !prevMobileRef.current) {
        leftSidebarVisible$.set(false);
      }
      prevMobileRef.current = mobile;
      setIsMobile(mobile);
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
            toastStepStartError(toast, error);
          });

          const newSearchParams = new URLSearchParams(searchParams);
          newSearchParams.delete('step');
          const queryString = newSearchParams.toString();
          const url = chatRoute(conversationId, queryString);
          navigate(url, { replace: true });
        } else {
          setTimeout(checkAndStart, 100);
        }
      };

      checkAndStart();
    }
  }, [stepParam, conversationId, isConnected, api, navigate, searchParams, toast]);

  // Fetch conversations from primary server
  const { data, isError, error, isLoading, isFetching, fetchNextPage, hasNextPage, refetch } =
    useConversationsInfiniteQuery();

  // Fetch conversations from secondary connected servers
  const { secondaryConversations, connectedServerCount } = useSecondaryServerConversations();
  const registry = use$(serverRegistry$);

  const apiConversations = useMemo(() => {
    const primaryServer = registry.servers.find((s) => s.id === registry.activeServerId);
    const all =
      data?.pages.flatMap(
        (page: { conversations: ConversationSummary[]; nextCursor: string | undefined }) =>
          (page.conversations ?? []).map((conv) => ({
            ...conv,
            serverId: registry.activeServerId,
            serverName: primaryServer?.name,
          }))
      ) ?? [];
    // Deduplicate across pages (overlapping pagination can produce duplicates)
    const seen = new Set<string>();
    return all.filter((conv) => {
      if (seen.has(conv.id)) return false;
      seen.add(conv.id);
      return true;
    });
  }, [data, registry.activeServerId, registry.servers]);

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

  // Reactive computation for store conversations using Legend State
  const storeConversations$ = useObservable(() => {
    const storeConvs = Array.from(conversations$.get().entries())
      .filter(([id, state]) => {
        // Only include non-demo conversations that have actual data
        const isDemoConv = demoItems.some((demo) => demo.id === id);
        return !isDemoConv && state.data.log && state.data.log.length > 0;
      })
      .map(([id, state]): ConversationSummary => {
        const firstTimestamp = state.data.log?.[0]?.timestamp;
        const lastTimestamp = state.lastMessage?.timestamp;
        return {
          id,
          name: state.data.name || 'New conversation',
          // Convert to seconds (groupByDate expects Unix seconds, not ms)
          created: firstTimestamp ? new Date(firstTimestamp).getTime() / 1000 : undefined,
          modified: lastTimestamp ? new Date(lastTimestamp).getTime() / 1000 : Date.now() / 1000,
          messages: state.data.log?.length || 0,
          workspace: state.data.workspace || '.',
          readonly: false,
        };
      });
    return storeConvs;
  });

  const showServerLabels = connectedServerCount > 1;

  const allConversations: ConversationSummary[] = useMemo(() => {
    const storeConvs = storeConversations$.get();

    // Combine demo, API (primary), secondary servers, store conversations
    // Deduplicate by serverId:id compound key (or just id for demos/store)
    const conversationMap = new Map<string, ConversationSummary>();

    // Add in order of preference: API items (most up-to-date), secondary, store items, demo items
    // Server-sourced items use serverId:id compound key so the same conversation name on
    // different servers is preserved.  Store/demo items (no serverId) use bare id and are
    // skipped when a server-sourced copy with the same id already exists.
    const seenIds = new Set<string>();
    [...apiItems, ...secondaryConversations, ...storeConvs, ...demoItems].forEach((conv) => {
      const key = conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id;
      if (conv.serverId) {
        // Server items: deduplicate by compound key only — allow same id across servers
        if (!conversationMap.has(key)) {
          conversationMap.set(key, conv);
        }
      } else {
        // Store/demo items: also check bare id to avoid duplicating server entries
        if (!conversationMap.has(key) && !seenIds.has(conv.id)) {
          conversationMap.set(key, conv);
        }
      }
      seenIds.add(conv.id);
    });

    return Array.from(conversationMap.values());
  }, [demoItems, apiItems, secondaryConversations, storeConversations$]);

  const handleSelectConversation = useCallback(
    (id: string, serverId?: string) => {
      if (id === selectedConversation$.get()) {
        return;
      }
      queryClient.cancelQueries({
        queryKey: ['conversation', selectedConversation$.get()],
      });
      selectedConversation$.set(id);

      const newParams = new URLSearchParams(searchParams);
      newParams.delete('split');
      newParams.delete('external');
      if (serverId) {
        newParams.set('server', serverId);
      } else {
        newParams.delete('server');
      }
      const queryString = newParams.toString();
      const url = chatRoute(id, queryString);
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

  const getSelectedConversationSummary = useCallback(
    (selectedConversation: string): ConversationSummary => {
      const conversation = allConversations.find((conv) => conv.id === selectedConversation);
      if (conversation) return conversation;

      const storeConversation = conversations$.get(selectedConversation)?.get();
      if (storeConversation?.data) {
        // Create conversation summary even if no messages yet - let ConversationContent handle loading
        return {
          id: selectedConversation,
          name: storeConversation.data.name || 'New conversation',
          modified: storeConversation.lastMessage
            ? new Date(storeConversation.lastMessage.timestamp || Date.now()).getTime()
            : Date.now(),
          messages: storeConversation.data.log?.length || 0,
          workspace: storeConversation.data.workspace || '.',
          readonly: false,
        };
      }

      // Even if not in store yet, create a minimal conversation to trigger ConversationContent loading
      return {
        id: selectedConversation,
        name: 'Loading...',
        modified: Date.now(),
        messages: 0,
        workspace: '.',
        readonly: false,
      };
    },
    [allConversations]
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
      conversation$.set(getSelectedConversationSummary(selectedConversation));
    } else {
      conversation$.set(undefined);
    }
  });

  useEffect(() => {
    const selectedId = selectedConversation$.get();
    if (selectedId) {
      conversation$.set(getSelectedConversationSummary(selectedId));
    } else {
      // Ensure conversation is cleared when no conversation is selected
      conversation$.set(undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allConversations, getSelectedConversationSummary]); // conversation$ is an observable we're setting, not reading

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

  // Handle navigating a split pane to a different conversation
  const handleNavigatePane = useCallback(
    (paneIndex: 0 | 1, newId: string, _newServerId?: string) => {
      if (!splitIds) return;
      const ids = [...splitIds];
      ids[paneIndex] = newId;
      const params = new URLSearchParams(searchParams);
      params.set('split', `${ids[0]},${ids[1]}`);
      // NOTE: We intentionally do not update the shared server= param here.
      // Both panes share one server= URL param; updating it for one pane silently
      // breaks the other. Per-pane server tracking is tracked for a future slice.
      navigate(`?${params.toString()}`);
    },
    [splitIds, searchParams, navigate]
  );

  // Handle "Open in split view" from a conversation list context menu
  const handleOpenInSplitView = useCallback(
    (conversationId: string) => {
      const params = new URLSearchParams(searchParams);
      if (splitIds) {
        // Already in split view: put clicked conversation in right pane, keep left
        params.set('split', `${splitIds[0]},${conversationId}`);
      } else {
        const currentId = selectedConversation$.get();
        if (currentId) {
          params.set('split', `${currentId},${conversationId}`);
        } else {
          params.set('split', `${conversationId},${conversationId}`);
        }
      }
      navigate(`?${params.toString()}`);
    },
    [splitIds, searchParams, navigate]
  );

  // Keyboard shortcut: Ctrl+Shift+\ (Cmd+Shift+\ on Mac) to toggle split view
  useEffect(() => {
    const toggleSplit = (e: KeyboardEvent) => {
      if (e.code !== 'Backslash' || !e.shiftKey || !(e.ctrlKey || e.metaKey)) return;
      const target = e.target as HTMLElement | null;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target?.isContentEditable
      ) {
        return;
      }
      if (splitIds) {
        // Close split view
        e.preventDefault();
        const params = new URLSearchParams(searchParams);
        params.delete('split');
        const qs = params.toString();
        navigate(chatRoute(splitIds[0], qs));
      } else {
        // Open split view
        const conversation = conversation$.get();
        if (!conversation) return;
        e.preventDefault();
        const params = new URLSearchParams(searchParams);
        params.set('split', `${conversation.id},${conversation.id}`);
        navigate(`?${params.toString()}`);
      }
    };

    document.addEventListener('keydown', toggleSplit);
    return () => document.removeEventListener('keydown', toggleSplit);
  }, [splitIds, navigate, searchParams, conversation$]);

  // Render main content based on current section
  const renderMainContent = () => {
    if (currentSection === 'agents') {
      return <AgentsView conversations={allConversations} />;
    }

    if (currentSection === 'workspaces') {
      return <WorkspacesView conversations={allConversations} />;
    }

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

    // Chat section — split view
    if (splitIds) {
      const leftConversation = getSelectedConversationSummary(splitIds[0]);
      const rightConversation = getSelectedConversationSummary(splitIds[1]);

      return (
        <SplitConversationView
          leftId={splitIds[0]}
          rightId={splitIds[1]}
          allConversations={allConversations}
          serverId={serverParam || undefined}
          leftIsReadOnly={leftConversation.readonly}
          rightIsReadOnly={rightConversation.readonly}
          vertical={isMobile}
          onNavigateLeft={(id, serverId) => handleNavigatePane(0, id, serverId)}
          onNavigateRight={(id, serverId) => handleNavigatePane(1, id, serverId)}
          onClose={() => {
            const params = new URLSearchParams(searchParams);
            params.delete('split');
            const qs = params.toString();
            navigate(chatRoute(splitIds[0], qs));
          }}
        />
      );
    }

    // Chat section — single conversation
    const conversation = conversation$.get();
    if (conversation) {
      return (
        <div className="flex h-full flex-col overflow-hidden">
          <div className="flex flex-shrink-0 items-center justify-end border-b px-2 py-0.5">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => {
                const params = new URLSearchParams(searchParams);
                params.set('split', `${conversation.id},${conversation.id}`);
                navigate(`?${params.toString()}`);
              }}
              title="Open in split view"
            >
              <Columns2 className="h-3 w-3" />
              Split
            </Button>
          </div>
          <div className="min-h-0 flex-1">
            <ConversationContent
              key={conversation.id}
              conversationId={conversation.id}
              serverId={serverParam || conversation.serverId}
              isReadOnly={conversation.readonly}
            />
          </div>
        </div>
      );
    }

    // If a conversationId is in the URL but not loaded yet, show nothing (avoid flash)
    if (conversationId) {
      return null;
    }

    // External session selected from sidebar — show transcript inline in the native chat layout
    if (externalParam) {
      return (
        <div className="h-full overflow-hidden">
          <ExternalSessionDetail key={externalParam} sessionId={externalParam} />
        </div>
      );
    }

    return (
      <div className="flex h-full flex-1 items-center justify-center">
        <WelcomeView />
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
            <nav aria-label="Conversations" className="min-h-0 flex-1">
              <UnifiedSidebar
                conversations={allConversations}
                selectedConversationId$={selectedConversation$}
                selectedExternalId={externalParam ?? undefined}
                onSelectConversation={(id, serverId) => {
                  handleSelectConversation(id, serverId);
                  leftSidebarVisible$.set(false);
                }}
                conversationsLoading={isLoading}
                conversationsFetching={isFetching}
                conversationsError={isError}
                conversationsErrorObj={error as Error}
                onConversationsRetry={() => refetch()}
                fetchNextPage={fetchNextPage}
                hasNextPage={hasNextPage}
                showServerLabels={showServerLabels}
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
                onOpenInSplitView={handleOpenInSplitView}
              />
            </nav>
          </SheetContent>
        </Sheet>

        {/* Main Content */}
        <main role="main" aria-label="Chat content" className="flex-1 overflow-hidden">
          {renderMainContent()}
        </main>

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

        {/* Mobile Bottom Navigation */}
        <MobileBottomNav />

        {/* Headless SettingsModal mount — desktop has it inside SidebarIcons, mobile needs it here */}
        <SettingsModal />

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
          <nav aria-label="Conversations" className="h-full">
            <UnifiedSidebar
              conversations={allConversations}
              selectedConversationId$={selectedConversation$}
              selectedExternalId={externalParam ?? undefined}
              onSelectConversation={handleSelectConversation}
              conversationsLoading={isLoading}
              conversationsFetching={isFetching}
              conversationsError={isError}
              conversationsErrorObj={error as Error}
              onConversationsRetry={() => refetch()}
              fetchNextPage={fetchNextPage}
              hasNextPage={hasNextPage}
              showServerLabels={showServerLabels}
              tasks={tasks}
              selectedTaskId={selectedTaskId || undefined}
              onSelectTask={handleSelectTask}
              onCreateTask={() => setShowCreateTaskDialog(true)}
              tasksLoading={tasksLoading}
              tasksError={!!tasksError}
              onTasksRetry={() => refetchTasks()}
              onOpenInSplitView={handleOpenInSplitView}
            />
          </nav>
        </ResizablePanel>

        <ResizableHandle />

        <ResizablePanel defaultSize={60} minSize={30} className="overflow-hidden">
          <main role="main" aria-label="Chat content" className="h-full overflow-hidden">
            {renderMainContent()}
          </main>
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

        {/* Fixed navigation icons for chat - only visible when a conversation is selected */}
        <Memo>
          {() => {
            const conversation = conversation$.get();
            return currentSection === 'chat' && conversation ? (
              <div className="w-12 flex-shrink-0">
                <RightSidebar conversationId={conversation.id} />
              </div>
            ) : null;
          }}
        </Memo>
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
