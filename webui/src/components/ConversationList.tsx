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
  Star,
  ArrowUpDown,
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
import { formatTokens } from '@/utils/conversationCost';
import { useApi } from '@/contexts/ApiContext';
import { demoConversations } from '@/democonversations';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
} from '@/utils/exportConversation';
import { DeleteConversationConfirmationDialog } from './DeleteConversationConfirmationDialog';

import { useConversationMetadata } from '@/hooks/useConversationMetadata';
import type { ConversationSummary } from '@/types/conversation';
import { type FC, useRef, useEffect, useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Computed, use$ } from '@legendapp/state/react';
import { type Observable } from '@legendapp/state';
import { conversations$ } from '@/stores/conversations';
import { toast } from 'sonner';

type SortBy = 'recent' | 'longest' | 'alpha';
const SORT_STORAGE_KEY = 'gptme:conv-sort';
const SORT_VALUES: SortBy[] = ['recent', 'longest', 'alpha'];

function readSortPreference(): SortBy {
  try {
    const raw = localStorage.getItem(SORT_STORAGE_KEY);
    return (SORT_VALUES.includes(raw as SortBy) ? raw : 'recent') as SortBy;
  } catch {
    return 'recent';
  }
}

function writeSortPreference(value: SortBy): void {
  try {
    localStorage.setItem(SORT_STORAGE_KEY, value);
  } catch {
    // Silently ignore storage errors (restricted environments, quota exceeded)
  }
}

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

  const { toggleStar } = useConversationMetadata();
  const queryClient = useQueryClient();
  const [showStarredOnly, setShowStarredOnly] = useState(false);

  const [sortBy, setSortBy] = useState<SortBy>(readSortPreference);
  const useInfiniteScroll = sortBy === 'recent';
  const handleSortChange = (value: SortBy) => {
    setSortBy(value);
    writeSortPreference(value);
  };

  // Separately track local star state for optimistic UI updates.
  // Keyed by conversation ID, value is the optimistic starred value.
  const [optimisticStars, setOptimisticStars] = useState<Record<string, boolean>>({});

  // Determine effective starred state: optimistic (if set) > server state
  const getIsStarred = useCallback(
    (conv: ConversationSummary) => {
      if (conv.id in optimisticStars) return optimisticStars[conv.id];
      return conv.starred ?? false;
    },
    [optimisticStars]
  );

  // Context menu state
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  // Guard against double-submit: onKeyDown(Enter) sets this before onBlur fires
  const renameCommittedRef = useRef(false);
  const filterInputRef = useRef<HTMLInputElement>(null);

  // URL-synced search state: local state for immediate input responsiveness,
  // with debounced writes to ?search= URL param.
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSearch = searchParams.get('search') ?? '';
  const [filterQuery, setFilterQuery] = useState(urlSearch);
  const filterDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track our own URL writes to avoid echo-syncing back to local state.
  const prevUrlSearchRef = useRef(urlSearch);

  const handleExportMarkdown = useCallback((conv: ConversationSummary) => {
    const storeConv = conversations$.get(conv.id)?.peek();
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
    const storeConv = conversations$.get(conv.id)?.peek();
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
    const storeConv = conversations$.get(conv.id)?.peek();
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

  // Sync URL → local state on browser back/forward (skip our own debounced writes).
  // Cancel any pending debounced write so a navigation event can't overwrite the new URL.
  useEffect(() => {
    if (prevUrlSearchRef.current !== urlSearch) {
      if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current);
      prevUrlSearchRef.current = urlSearch;
      setFilterQuery(urlSearch);
    }
  }, [urlSearch]);

  // Flush debounce timer on unmount.
  useEffect(() => {
    return () => {
      if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current);
    };
  }, []);

  const handleFilterChange = useCallback(
    (value: string) => {
      setFilterQuery(value);
      if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current);
      filterDebounceRef.current = setTimeout(() => {
        prevUrlSearchRef.current = value;
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (value) next.set('search', value);
            else next.delete('search');
            return next;
          },
          { replace: true }
        );
      }, 300);
    },
    [setSearchParams]
  );

  // Refs for infinite scrolling
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const loadMoreSentinelRef = useRef<HTMLDivElement>(null);
  const observer = useRef<IntersectionObserver | null>(null);
  const isFetchingRef = useRef(isFetching);
  isFetchingRef.current = isFetching;

  // Keep infinite scroll for the date-grouped default view only.
  // Flat sorts use an explicit pager so loaded pages do not reshuffle mid-scroll.
  useEffect(() => {
    if (observer.current) observer.current.disconnect();

    const container = scrollContainerRef.current;
    const sentinel = loadMoreSentinelRef.current;

    if (!useInfiniteScroll || !container || !sentinel || !hasNextPage) {
      return;
    }

    observer.current = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry.isIntersecting && hasNextPage && !isFetchingRef.current) {
          const containerHeight = container.clientHeight;
          const scrollHeight = container.scrollHeight;
          const scrollTop = container.scrollTop;

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
  }, [useInfiniteScroll, hasNextPage, fetchNextPage]); // isFetching accessed via ref to avoid observer recreation

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

  useEffect(() => {
    const handleSlashShortcut = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) return;
      if (!filterInputRef.current) return;
      // Don't steal '/' from other focused inputs/textareas/contenteditable
      const active = document.activeElement;
      if (
        active instanceof HTMLInputElement ||
        active instanceof HTMLTextAreaElement ||
        active instanceof HTMLSelectElement ||
        (active instanceof HTMLElement && active.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      filterInputRef.current.focus();
      filterInputRef.current.select();
    };

    window.addEventListener('keydown', handleSlashShortcut);
    return () => window.removeEventListener('keydown', handleSlashShortcut);
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
    const storeConv = conversations$.get(conv.id)?.peek();
    return storeConv?.data?.name || conv.name || stripDate(conv.id);
  }

  const normalizedFilter = filterQuery.trim().toLowerCase();
  const matchesFilter = (conv: ConversationSummary) =>
    !normalizedFilter || getConversationName(conv).toLowerCase().includes(normalizedFilter);

  function highlightText(text: string, query: string) {
    if (!query) return text;
    const idx = text.toLowerCase().indexOf(query);
    if (idx === -1) return text;
    return (
      <>
        {text.slice(0, idx)}
        <mark className="rounded-sm bg-yellow-200 px-0 text-yellow-900 dark:bg-yellow-700 dark:text-yellow-100">
          {text.slice(idx, idx + query.length)}
        </mark>
        {text.slice(idx + query.length)}
      </>
    );
  }

  const ConversationItem: FC<{ conv: ConversationSummary; showLabel?: boolean }> = ({
    conv,
    showLabel,
  }) => {
    // For demo conversations, get messages from demoConversations
    const demoConv = demoConversations.find((dc) => dc.id === conv.id);
    const isDemo = !!demoConv;
    const isRenaming = renamingId === conv.id;

    const conversationContent = (
      <Computed>
        {() => {
          const convState = conversations$.get(conv.id)?.peek();
          const isSelected = selectedId$?.get() === conv.id;

          return (
            <div
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              className={`group cursor-pointer rounded-lg py-2 pl-2 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${
                isSelected ? 'bg-accent' : ''
              }`}
              onClick={() => onSelect(conv.id, conv.serverId)}
              onKeyDown={(e) => {
                // Only act when the row itself is focused, so Enter/Space inside
                // the inline rename input doesn't also trigger selection.
                if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault();
                  onSelect(conv.id, conv.serverId);
                }
              }}
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
                    {!demoIds.has(conv.id) && (
                      <button
                        className={`shrink-0 rounded p-0.5 transition-colors hover:text-yellow-500 focus:outline-none focus-visible:text-yellow-500 focus-visible:opacity-100 ${
                          getIsStarred(conv)
                            ? 'text-yellow-500 opacity-100'
                            : 'text-muted-foreground opacity-0 focus-visible:opacity-100 group-hover:opacity-100'
                        }`}
                        onClick={async (e) => {
                          e.stopPropagation();
                          const newVal = !getIsStarred(conv);
                          // Optimistic update
                          setOptimisticStars((prev) => ({ ...prev, [conv.id]: newVal }));
                          await toggleStar(conv.id, getIsStarred(conv));
                          // Invalidate cache so server state reflects the toggle before
                          // the optimistic entry is cleared.
                          await queryClient.invalidateQueries({ queryKey: ['conversations'] });
                          setOptimisticStars((prev) => {
                            const next = { ...prev };
                            delete next[conv.id];
                            return next;
                          });
                        }}
                        title={getIsStarred(conv) ? 'Unstar conversation' : 'Star conversation'}
                        aria-label={
                          getIsStarred(conv) ? 'Unstar conversation' : 'Star conversation'
                        }
                      >
                        <Star
                          className="h-3 w-3"
                          fill={getIsStarred(conv) ? 'currentColor' : 'none'}
                        />
                      </button>
                    )}
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
                      {highlightText(
                        convState?.data?.name || conv.name || stripDate(conv.id),
                        normalizedFilter
                      )}
                    </div>
                    {!!conv.messages && (
                      <span className="inline-flex shrink-0 items-center whitespace-nowrap rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                        <MessageSquare className="mr-1 h-3 w-3" />
                        {conv.messages}
                      </span>
                    )}
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

                  {(() => {
                    const summaryTokens =
                      (conv.total_input_tokens ?? 0) +
                      (conv.total_output_tokens ?? 0) +
                      (conv.total_cache_read_tokens ?? 0) +
                      (conv.total_cache_creation_tokens ?? 0);
                    if (summaryTokens === 0) return null;
                    return (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="flex items-center text-muted-foreground">
                            <span className="text-[10px] text-muted-foreground/60">
                              {formatTokens(summaryTokens)} tok
                            </span>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          <div className="space-y-1 text-xs">
                            <div className="font-medium">Token estimate</div>
                            {(conv.total_input_tokens ?? 0) > 0 && (
                              <div>Input: {formatTokens(conv.total_input_tokens!)} tokens</div>
                            )}
                            {(conv.total_output_tokens ?? 0) > 0 && (
                              <div>Output: {formatTokens(conv.total_output_tokens!)} tokens</div>
                            )}
                            {(conv.total_cache_read_tokens ?? 0) > 0 && (
                              <div>
                                Cache read: {formatTokens(conv.total_cache_read_tokens!)} tokens
                              </div>
                            )}
                            {(conv.total_cache_creation_tokens ?? 0) > 0 && (
                              <div>
                                Cache create: {formatTokens(conv.total_cache_creation_tokens!)}{' '}
                                tokens
                              </div>
                            )}
                            <div>Total: {formatTokens(summaryTokens)} tokens</div>
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    );
                  })()}

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
          {!demoIds.has(conv.id) && (
            <ContextMenuItem
              onClick={(e) => {
                e.stopPropagation();
                const newVal = !getIsStarred(conv);
                setOptimisticStars((prev) => ({ ...prev, [conv.id]: newVal }));
                toggleStar(conv.id, getIsStarred(conv))
                  .then(async () => {
                    await queryClient.invalidateQueries({ queryKey: ['conversations'] });
                    setOptimisticStars((prev) => {
                      const next = { ...prev };
                      delete next[conv.id];
                      return next;
                    });
                  })
                  .catch((err) => {
                    console.error('Failed to toggle star from context menu:', err);
                    setOptimisticStars((prev) => {
                      const next = { ...prev };
                      delete next[conv.id];
                      return next;
                    });
                  });
              }}
            >
              <Star className="mr-2 h-4 w-4" fill={getIsStarred(conv) ? 'currentColor' : 'none'} />
              {getIsStarred(conv) ? 'Unstar' : 'Star'}
            </ContextMenuItem>
          )}
          <ContextMenuSeparator />
          <ContextMenuItem
            className="text-destructive focus:text-destructive"
            onClick={(e) => {
              e.stopPropagation();
              const storeConv = conversations$.get(conv.id)?.peek();
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

  // Filter by search query AND star state (when showStarredOnly is active)
  const filteredRealConversations = realConversations.filter(
    (c) => matchesFilter(c) && (!showStarredOnly || getIsStarred(c))
  );
  const filteredDemos = demos.filter(matchesFilter);
  const hasFilter = normalizedFilter.length > 0;
  const hasNoFilteredMatches =
    (hasFilter || showStarredOnly) &&
    filteredRealConversations.length === 0 &&
    filteredDemos.length === 0;

  // Sort filtered conversations. 'recent' keeps server order (modified desc); others sort flat.
  const sortedRealConversations =
    sortBy === 'recent'
      ? filteredRealConversations
      : filteredRealConversations.slice().sort((a, b) => {
          if (sortBy === 'longest') return (b.messages ?? 0) - (a.messages ?? 0);
          // alpha
          return getConversationName(a).localeCompare(getConversationName(b));
        });

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
              onChange={(e) => handleFilterChange(e.target.value)}
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
                  handleFilterChange('');
                  filterInputRef.current?.focus();
                }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          {/* Star filter toggle + sort control */}
          {realConversations.length > 0 && (
            <div className="flex items-center justify-between px-1 pt-1">
              <button
                aria-label={showStarredOnly ? 'Show all conversations' : 'Show starred only'}
                aria-pressed={showStarredOnly}
                className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs transition-colors ${
                  showStarredOnly
                    ? 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                onClick={() => setShowStarredOnly((v) => !v)}
              >
                <Star className="h-3 w-3" fill={showStarredOnly ? 'currentColor' : 'none'} />
                {showStarredOnly ? 'Starred' : 'All'}
              </button>
              <button
                aria-label={`Sort conversations: ${sortBy === 'recent' ? 'Recent' : sortBy === 'longest' ? 'Longest' : 'A-Z'} (click to cycle)`}
                className="flex items-center gap-1 rounded px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
                onClick={() => {
                  const next: SortBy =
                    sortBy === 'recent' ? 'longest' : sortBy === 'longest' ? 'alpha' : 'recent';
                  handleSortChange(next);
                }}
                title={`Sort: ${sortBy === 'recent' ? 'Recent' : sortBy === 'longest' ? 'Longest' : 'A-Z'} (click to cycle)`}
              >
                <ArrowUpDown className="h-3 w-3" />
                {sortBy === 'recent' ? 'Recent' : sortBy === 'longest' ? 'Longest' : 'A-Z'}
              </button>
            </div>
          )}
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
          {showStarredOnly && !hasFilter
            ? 'No starred conversations.'
            : 'No conversations match your search.'}
        </div>
      )}

      {/* Render real conversations: grouped by date for 'recent', flat list for other sorts */}
      {!isLoading &&
        !isError &&
        sortBy === 'recent' &&
        groupByDate<ConversationSummary>(
          sortedRealConversations,
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
      {!isLoading && !isError && sortBy !== 'recent' && (
        <div className="space-y-2 px-2">
          {sortedRealConversations.map((conv) => (
            <ConversationItem
              key={conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id}
              conv={conv}
              showLabel={showServerLabels}
            />
          ))}
        </div>
      )}

      {/* Loading indicator for fetching more */}
      {isFetching && !isLoading && (
        <div className="flex items-center justify-center p-4 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading more conversations...
        </div>
      )}

      {/* Keep flat sorts stable while still allowing more pages on demand. */}
      {!isLoading && !isError && sortBy !== 'recent' && hasNextPage && (
        <div className="px-2 pb-2">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => fetchNextPage()}
            disabled={isFetching}
          >
            {isFetching ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading more conversations...
              </>
            ) : (
              'Load more conversations'
            )}
          </Button>
        </div>
      )}

      {/* Sentinel element for infinite loading */}
      {useInfiniteScroll && <div ref={loadMoreSentinelRef} style={{ height: '1px' }} />}

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
