import {
  Clock,
  MessageSquare,
  Lock,
  Loader2,
  Signal,
  Pencil,
  Download,
  FileText,
  FileJson,
  Trash2,
  BookOpen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { getRelativeTimeString, groupByDate } from '@/utils/time';
import { useApi } from '@/contexts/ApiContext';
import { demoConversations, getDemoMessages } from '@/democonversations';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
} from '@/utils/exportConversation';
import { DeleteConversationConfirmationDialog } from './DeleteConversationConfirmationDialog';

import type { MessageRole, ConversationSummary } from '@/types/conversation';
import { type FC, useRef, useEffect, useState, useCallback } from 'react';
import { Computed, use$ } from '@legendapp/state/react';
import { type Observable } from '@legendapp/state';
import { conversations$ } from '@/stores/conversations';
import { toast } from 'sonner';

type MessageBreakdown = Partial<Record<MessageRole, number>>;

interface Props {
  conversations: ConversationSummary[];
  onSelect: (id: string, serverId?: string) => void;
  isLoading?: boolean;
  isFetching?: boolean;
  isError?: boolean;
  error?: Error;
  onRetry?: () => void;
  fetchNextPage: () => void;
  hasNextPage?: boolean;
  selectedId$?: Observable<string | null>;
  showServerLabels?: boolean;
}

export const ConversationList: FC<Props> = ({
  conversations,
  onSelect,
  isLoading = false,
  isFetching = false,
  isError = false,
  error,
  onRetry,
  fetchNextPage,
  hasNextPage = false,
  selectedId$,
  showServerLabels = false,
}) => {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);

  // Context menu state
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  // Guard against double-submit: onKeyDown(Enter) sets this before onBlur fires
  const renameCommittedRef = useRef(false);

  const handleExportMarkdown = useCallback((conv: ConversationSummary) => {
    const storeConv = conversations$.get(conv.id)?.get();
    const messages = storeConv?.data?.log ?? [];
    const name = storeConv?.data?.name || conv.name || conv.id;
    if (messages.length === 0) {
      toast.error('No messages to export. Open the conversation first.');
      return;
    }
    exportConversationAsMarkdown(conv.id, name, getExportableMessages(messages));
    toast.success('Exported as Markdown');
  }, []);

  const handleExportJSON = useCallback((conv: ConversationSummary) => {
    const storeConv = conversations$.get(conv.id)?.get();
    const messages = storeConv?.data?.log ?? [];
    const name = storeConv?.data?.name || conv.name || conv.id;
    if (messages.length === 0) {
      toast.error('No messages to export. Open the conversation first.');
      return;
    }
    exportConversationAsJSON(conv.id, name, getExportableMessages(messages));
    toast.success('Exported as JSON');
  }, []);

  const handleStartRename = useCallback((conv: ConversationSummary) => {
    const storeConv = conversations$.get(conv.id)?.get();
    const currentName = storeConv?.data?.name || conv.name || conv.id;
    renameCommittedRef.current = false;
    setRenamingId(conv.id);
    setRenameValue(currentName);
  }, []);

  const handleRenameCancel = useCallback(() => {
    renameCommittedRef.current = true;
    setRenamingId(null);
  }, []);

  const handleRenameSubmit = useCallback(
    async (convId: string) => {
      if (renameCommittedRef.current) return;
      renameCommittedRef.current = true;

      const trimmed = renameValue.trim();
      if (!trimmed) {
        setRenamingId(null);
        return;
      }
      try {
        const currentConfig = await api.getChatConfig(convId);
        await api.updateChatConfig(convId, {
          ...currentConfig,
          chat: { ...currentConfig.chat, name: trimmed },
        });
        // Update local store
        const conv = conversations$.get(convId);
        if (conv?.data) {
          conv.data.name.set(trimmed);
        }
        toast.success('Conversation renamed');
      } catch {
        toast.error('Failed to rename conversation');
      }
      setRenamingId(null);
    },
    [api, renameValue]
  );

  // Refs for infinite scrolling
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const loadMoreSentinelRef = useRef<HTMLDivElement>(null);
  const observer = useRef<IntersectionObserver | null>(null);
  const isFetchingRef = useRef(isFetching);
  isFetchingRef.current = isFetching;

  // Set up intersection observer for infinite scrolling
  useEffect(() => {
    if (observer.current) observer.current.disconnect();

    // Only set up observer if we have content and can scroll
    const container = scrollContainerRef.current;
    const sentinel = loadMoreSentinelRef.current;

    if (!container || !sentinel || !hasNextPage) {
      return;
    }

    observer.current = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry.isIntersecting && hasNextPage && !isFetchingRef.current) {
          // Additional check: ensure we actually have scrollable content or are near the bottom
          const containerHeight = container.clientHeight;
          const scrollHeight = container.scrollHeight;
          const scrollTop = container.scrollTop;

          // Load if we have scrollable content and are near the bottom, OR if content doesn't fill container yet
          const hasScrollableContent = scrollHeight > containerHeight;
          const nearBottom = scrollTop + containerHeight >= scrollHeight - 100;

          if (!hasScrollableContent || nearBottom) {
            console.log('[ConversationList] Loading more conversations...');
            fetchNextPage();
          }
        }
      },
      {
        root: container,
        threshold: 0.1,
        rootMargin: '0px 0px 50px 0px',
      }
    );

    observer.current.observe(sentinel);

    return () => {
      if (observer.current) observer.current.disconnect();
    };
  }, [hasNextPage, fetchNextPage]); // isFetching accessed via ref to avoid observer recreation

  if (!conversations) {
    return null;
  }

  // Separate demo conversations from real ones
  const demoIds = new Set(demoConversations.map((d) => d.id));
  const realConversations = conversations.filter((c) => !demoIds.has(c.id));
  const demos = conversations.filter((c) => demoIds.has(c.id));

  // strip leading YYYY-MM-DD from name if present
  function stripDate(name: string) {
    const match = name.match(/^\d{4}-\d{2}-\d{2}[- ](.*)/);
    return match ? match[1] : name;
  }

  const ConversationItem: FC<{ conv: ConversationSummary; showLabel?: boolean }> = ({
    conv,
    showLabel,
  }) => {
    // For demo conversations, get messages from demoConversations
    const demoConv = demoConversations.find((dc) => dc.id === conv.id);
    const isDemo = !!demoConv;
    const isRenaming = renamingId === conv.id;

    // For API conversations, fetch messages
    const getMessageBreakdown = (): MessageBreakdown => {
      if (demoConv) {
        const messages = getDemoMessages(demoConv.id);
        return messages.reduce((acc: MessageBreakdown, msg) => {
          acc[msg.role] = (acc[msg.role] || 0) + 1;
          return acc;
        }, {});
      }

      // Get messages from store
      const storeConv = conversations$.get(conv.id)?.get();
      // Return empty breakdown if conversation or data is not loaded yet
      if (!storeConv?.data?.log) return {};

      return storeConv.data.log.reduce((acc: MessageBreakdown, msg$) => {
        const role = msg$.role;
        if (role && typeof role === 'string') {
          acc[role as MessageRole] = (acc[role as MessageRole] || 0) + 1;
        }
        return acc;
      }, {} as MessageBreakdown);
    };

    const formatBreakdown = (breakdown: MessageBreakdown) => {
      const order: MessageRole[] = ['user', 'assistant', 'system', 'tool'];
      return Object.entries(breakdown)
        .sort(([a], [b]) => {
          const aIndex = order.indexOf(a as MessageRole);
          const bIndex = order.indexOf(b as MessageRole);
          if (aIndex === -1 && bIndex === -1) return 0;
          if (aIndex === -1) return 1;
          if (bIndex === -1) return -1;
          return aIndex - bIndex;
        })
        .map(([role, count]) => `${role}: ${count}`)
        .join('\n');
    };

    const conversationContent = (
      <Computed>
        {() => {
          const convState = conversations$.get(conv.id)?.get();
          const isSelected = selectedId$?.get() === conv.id;

          return (
            <div
              className={`cursor-pointer rounded-lg py-2 pl-2 transition-colors hover:bg-accent ${
                isSelected ? 'bg-accent' : ''
              }`}
              onClick={() => onSelect(conv.id, conv.serverId)}
            >
              <div>
                {isRenaming ? (
                  <input
                    data-testid="conversation-rename-input"
                    className="mb-1 w-full rounded border border-input bg-background px-1 text-sm outline-none focus:ring-1 focus:ring-ring"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleRenameSubmit(conv.id);
                      } else if (e.key === 'Escape') {
                        handleRenameCancel();
                      }
                    }}
                    onBlur={() => handleRenameSubmit(conv.id)}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                  />
                ) : (
                  <div
                    data-testid="conversation-title"
                    className="font-small mb-1 whitespace-nowrap"
                    style={{
                      maskImage:
                        'linear-gradient(to right, black 0%, black calc(100% - 2rem), transparent 100%)',
                      WebkitMaskImage:
                        'linear-gradient(to right, black 0%, black calc(100% - 2rem), transparent 100%)',
                    }}
                  >
                    {convState?.data?.name || conv.name || stripDate(conv.id)}
                  </div>
                )}
                {conv.last_message_preview && (
                  <div
                    className="mb-1 truncate text-xs text-muted-foreground/70"
                    title={conv.last_message_preview}
                  >
                    {conv.last_message_role === 'user' ? '→ ' : '← '}
                    {conv.last_message_preview}
                  </div>
                )}
                <div className="flex items-center space-x-3 text-xs text-muted-foreground">
                  <Tooltip>
                    <TooltipTrigger>
                      <time
                        className="flex items-center whitespace-nowrap"
                        dateTime={new Date(conv.modified * 1000).toISOString()}
                      >
                        <Clock className="mr-1 h-3 w-3" />
                        {getRelativeTimeString(new Date(conv.modified * 1000))}
                      </time>
                    </TooltipTrigger>
                    <TooltipContent>
                      {new Date(conv.modified * 1000).toLocaleString()}
                    </TooltipContent>
                  </Tooltip>
                  <Computed>
                    {() => {
                      const storeConv = conversations$.get(conv.id)?.get();
                      const isLoaded = storeConv?.data?.log?.length > 0;

                      const breakdown = isLoaded ? getMessageBreakdown() : {};
                      const count = isLoaded
                        ? Object.values(breakdown).reduce((a, b) => a + b, 0)
                        : conv.messages;

                      const messageCountElement = (
                        <span className="flex items-center">
                          <MessageSquare className="mr-1 h-3 w-3" />
                          {count}
                        </span>
                      );

                      return isLoaded ? (
                        <Tooltip>
                          <TooltipTrigger asChild>{messageCountElement}</TooltipTrigger>
                          <TooltipContent>
                            <div className="whitespace-pre">{formatBreakdown(breakdown)}</div>
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        messageCountElement
                      );
                    }}
                  </Computed>

                  {/* Show conversation state indicators */}
                  <div className="flex items-center space-x-2">
                    {convState?.isConnected && (
                      <Tooltip>
                        <TooltipTrigger>
                          <span className="flex items-center">
                            <Signal className="h-3 w-3 text-primary" />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>Connected</TooltipContent>
                      </Tooltip>
                    )}
                    {convState?.isGenerating && (
                      <Tooltip>
                        <TooltipTrigger>
                          <span className="flex items-center">
                            <Loader2 className="h-3 w-3 animate-spin text-primary" />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>Generating...</TooltipContent>
                      </Tooltip>
                    )}
                    {convState?.pendingTool && (
                      <Tooltip>
                        <TooltipTrigger>
                          <span className="flex items-center">
                            <span className="text-lg">⚙️</span>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Pending tool: {convState.pendingTool.tooluse.tool}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    {conv.readonly && (
                      <Tooltip>
                        <TooltipTrigger>
                          <span className="flex items-center">
                            <Lock className="h-3 w-3" />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>This conversation is read-only</TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                  {showLabel && conv.serverName && (
                    <span className="ml-auto rounded bg-muted px-1 py-0.5 text-[10px]">
                      {conv.serverName}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        }}
      </Computed>
    );

    // Demo conversations don't support context menu actions
    if (isDemo) {
      return conversationContent;
    }

    return (
      <ContextMenu>
        <ContextMenuTrigger>{conversationContent}</ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem
            onClick={(e) => {
              e.stopPropagation();
              handleStartRename(conv);
            }}
          >
            <Pencil className="mr-2 h-4 w-4" />
            Rename
          </ContextMenuItem>
          <ContextMenuSub>
            <ContextMenuSubTrigger>
              <Download className="mr-2 h-4 w-4" />
              Export
            </ContextMenuSubTrigger>
            <ContextMenuSubContent>
              <ContextMenuItem onClick={() => handleExportMarkdown(conv)}>
                <FileText className="mr-2 h-4 w-4" />
                Markdown
              </ContextMenuItem>
              <ContextMenuItem onClick={() => handleExportJSON(conv)}>
                <FileJson className="mr-2 h-4 w-4" />
                JSON
              </ContextMenuItem>
            </ContextMenuSubContent>
          </ContextMenuSub>
          <ContextMenuSeparator />
          <ContextMenuItem
            className="text-destructive focus:text-destructive"
            onClick={(e) => {
              e.stopPropagation();
              const storeConv = conversations$.get(conv.id)?.get();
              const name = storeConv?.data?.name || conv.name || conv.id;
              setDeleteTarget({ id: conv.id, name });
            }}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Delete
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    );
  };

  return (
    <div
      ref={scrollContainerRef}
      data-testid="conversation-list"
      className="h-full space-y-2 overflow-y-auto overflow-x-hidden"
    >
      {isLoading && (
        <div className="flex items-center justify-center px-2 py-4 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading conversations...
        </div>
      )}
      {!isLoading && isError && (
        <div className="space-y-2 px-2 py-4 text-sm text-destructive">
          <div className="font-medium">Failed to load conversations</div>
          <div className="text-muted-foreground">{error?.message}</div>
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry} className="w-full">
              <Loader2 className="mr-2 h-4 w-4" />
              Retry
            </Button>
          )}
        </div>
      )}
      {!isLoading && !isError && !isConnected && conversations.length === 0 && (
        <div className="px-2 py-2 text-sm text-muted-foreground">
          Not connected to API. Use the connect button to load conversations.
        </div>
      )}
      {!isLoading && !isError && isConnected && conversations.length === 0 && (
        <div className="px-2 py-2 text-sm text-muted-foreground">
          No conversations found. Start a new conversation to get started.
        </div>
      )}

      {/* Render real conversations grouped by date */}
      {!isLoading &&
        !isError &&
        groupByDate<ConversationSummary>(realConversations, (c) => c.created ?? c.modified).map(
          ({ group, items }) => (
            <div key={group}>
              <div
                data-testid="date-group-header"
                className="sticky top-0 z-10 bg-background/95 px-2 py-1.5 text-xs font-medium text-muted-foreground backdrop-blur supports-[backdrop-filter]:bg-background/60"
              >
                {group}
              </div>
              <div className="space-y-2 px-2">
                {items.map((conv) => (
                  <ConversationItem
                    key={conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id}
                    conv={conv}
                    showLabel={showServerLabels}
                  />
                ))}
              </div>
            </div>
          )
        )}

      {/* Loading indicator for fetching more */}
      {isFetching && !isLoading && (
        <div className="flex items-center justify-center p-4 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading more conversations...
        </div>
      )}

      {/* Sentinel element for infinite loading */}
      <div ref={loadMoreSentinelRef} style={{ height: '1px' }} />

      {/* End message */}
      {!hasNextPage && realConversations.length > 0 && (
        <div className="py-4 text-center text-sm text-muted-foreground">
          You've reached the end of your conversations.
        </div>
      )}

      {/* Demo conversations pinned at bottom */}
      {!isLoading && !isError && demos.length > 0 && (
        <div>
          <div className="flex items-center gap-1 px-2 py-1.5 text-xs font-medium text-muted-foreground">
            <BookOpen className="h-3 w-3" />
            Getting Started
          </div>
          <div className="space-y-2 px-2">
            {demos.map((conv) => (
              <ConversationItem key={conv.id} conv={conv} />
            ))}
          </div>
        </div>
      )}

      {/* Delete confirmation dialog */}
      {deleteTarget && (
        <DeleteConversationConfirmationDialog
          conversationName={deleteTarget.id}
          displayName={deleteTarget.name}
          open={!!deleteTarget}
          onOpenChange={(open) => {
            if (!open) setDeleteTarget(null);
          }}
          onDelete={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
};
