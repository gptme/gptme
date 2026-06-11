import {
  Clock,
  MessageSquare,
  Lock,
  Loader2,
  Search,
  Signal,
  Pencil,
  Download,
  FileText,
  FileJson,
  Trash2,
  BookOpen,
  Columns2,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { computeConversationCost, formatCost, formatTokens } from '@/utils/conversationCost';
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
  onOpenInSplitView?: (conversationId: string) => void;
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
  onOpenInSplitView,
}) => {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);

  // Context menu state
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [filterQuery, setFilterQuery] = useState('');
  // Guard against double-submit: onKeyDown(Enter) sets this before onBlur fires
  const renameCommittedRef = useRef(false);
  const filterInputRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    const handleFilterShortcut = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'f' || !e.altKey || e.metaKey || e.ctrlKey) return;
      if (!filterInputRef.current) return;
      e.preventDefault();
      filterInputRef.current.focus();
      filterInputRef.current.select();
    };

    window.addEventListener('keydown', handleFilterShortcut);
    return () => window.removeEventListener('keydown', handleFilterShortcut);
  }, []);

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

  function getConversationName(conv: ConversationSummary) {
    const storeConv = conversations$.get(conv.id)?.get();
    return storeConv?.data?.name || conv.name || stripDate(conv.id);
  }

  const normalizedFilter = filterQuery.trim().toLowerCase();
  const matchesFilter = (conv: ConversationSummary) =>
    !normalizedFilter || getConversationName(conv).toLowerCase().includes(normalizedFilter);

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
                  <div className="mb-1 flex items-center gap-2">
                    <div
                      data-testid="conversation-title"
                      className="font-small min-w-0 flex-1 whitespace-nowrap"
                      style={{
                        maskImage:
                          'linear-gradient(to right, black 0%, black calc(100% - 2rem), transparent 100%)',
                        WebkitMaskImage:
                          'linear-gradient(to right, black 0%, black calc(100% - 2rem), transparent 100%)',
                      }}
                    >
                      {convState?.data?.name || conv.name || stripDate(conv.id)}
                    </div>
                    <Computed>
                      {() => {
                        const storeConv = conversations$.get(conv.id)?.get();
                        const isLoaded = storeConv?.data?.log?.length > 0;

                        const breakdown = isLoaded ? getMessageBreakdown() : {};
                        const count = isLoaded
                          ? Object.values(breakdown).reduce((a, b) => a + b, 0)
                          : conv.messages;

                        if (!count) {
                          return null;
                        }

                        const badge = (
                          <span className="inline-flex shrink-0 items-center whitespace-nowrap rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                            <MessageSquare className="mr-1 h-3 w-3" />
                            {count}
                          </span>
                        );

                        return isLoaded ? (
                          <Tooltip>
                            <TooltipTrigger asChild>{badge}</TooltipTrigger>
                            <TooltipContent>
                              <div className="whitespace-pre">{formatBreakdown(breakdown)}</div>
                            </TooltipContent>
                          </Tooltip>
                        ) : (
                          badge
                        );
                      }}
                    </Computed>
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

                  {/* Cost badge: show per-conversation total cost from loaded data */}
                  <Computed>
                    {() => {
                      const storeConv = conversations$.get(conv.id)?.get();
                      const isLoaded = storeConv?.data?.log?.length > 0;
                      if (!isLoaded) return null;

                      const cost = computeConversationCost(storeConv!.data!.log);
                      if (!cost.hasData) return null;

                      return (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="flex items-center text-muted-foreground">
                              <span>{formatCost(cost.totalCost)}</span>
                              <span className="ml-0.5 text-[10px] text-muted-foreground/60">
                                · {formatTokens(cost.totalTokens)}
                              </span>
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            <div className="space-y-1 text-xs">
                              <div className="font-medium">Session cost</div>
                              <div>Input: {formatTokens(cost.inputTokens)} tokens</div>
                              <div>Output: {formatTokens(cost.outputTokens)} tokens</div>
                              {cost.cacheReadTokens > 0 && (
                                <div>Cache read: {formatTokens(cost.cacheReadTokens)} tokens</div>
                              )}
                              {cost.cacheCreationTokens > 0 && (
                                <div>
                                  Cache create: {formatTokens(cost.cacheCreationTokens)} tokens
                                </div>
                              )}
                              <div>Total: {formatTokens(cost.totalTokens)} tokens</div>
                            </div>
                          </TooltipContent>
                        </Tooltip>
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
          {onOpenInSplitView && (
            <ContextMenuItem
              onClick={(e) => {
                e.stopPropagation();
                onOpenInSplitView(conv.id);
              }}
            >
              <Columns2 className="mr-2 h-4 w-4" />
              Open in split view
            </ContextMenuItem>
          )}
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

  const filteredRealConversations = realConversations.filter(matchesFilter);
  const filteredDemos = demos.filter(matchesFilter);
  const hasFilter = normalizedFilter.length > 0;
  const hasNoFilteredMatches =
    hasFilter && filteredRealConversations.length === 0 && filteredDemos.length === 0;

  return (
    <div
      ref={scrollContainerRef}
      data-testid="conversation-list"
      className="h-full space-y-2 overflow-y-auto overflow-x-hidden"
    >
      {!isLoading && !isError && conversations.length > 0 && (
        <div className="sticky top-0 z-20 bg-background/95 px-2 pb-1 pt-2 backdrop-blur supports-[backdrop-filter]:bg-background/60">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={filterInputRef}
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              placeholder="Search conversations"
              aria-label="Search conversations"
              className="h-8 pl-8 pr-8 text-sm"
            />
            {filterQuery && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Clear conversation search"
                className="absolute right-1 top-1/2 h-6 w-6 -translate-y-1/2"
                onClick={() => {
                  setFilterQuery('');
                  filterInputRef.current?.focus();
                }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>
      )}
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
      {!isLoading && !isError && hasNoFilteredMatches && (
        <div className="px-2 py-4 text-sm text-muted-foreground">
          No conversations match your search.
        </div>
      )}

      {/* Render real conversations grouped by date */}
      {!isLoading &&
        !isError &&
        groupByDate<ConversationSummary>(
          filteredRealConversations,
          (c) => c.created ?? c.modified
        ).map(({ group, items }) => (
          <div key={group}>
            <div
              data-testid="date-group-header"
              className="sticky top-11 z-10 bg-background/95 px-2 py-1.5 text-xs font-medium text-muted-foreground backdrop-blur supports-[backdrop-filter]:bg-background/60"
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
        ))}

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
      {!hasFilter && !hasNextPage && realConversations.length > 0 && (
        <div className="py-4 text-center text-sm text-muted-foreground">
          You've reached the end of your conversations.
        </div>
      )}

      {/* Demo conversations pinned at bottom */}
      {!isLoading && !isError && filteredDemos.length > 0 && (
        <div>
          <div className="flex items-center gap-1 px-2 py-1.5 text-xs font-medium text-muted-foreground">
            <BookOpen className="h-3 w-3" />
            Getting Started
          </div>
          <div className="space-y-2 px-2">
            {filteredDemos.map((conv) => (
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
