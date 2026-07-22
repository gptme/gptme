import { Loader2, Search, BookOpen, X, Star, ArrowUpDown, Layers } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { useApi } from '@/contexts/ApiContext';
import { groupByDate, getRelativeTimeString } from '@/utils/time';
import { demoConversations } from '@/democonversations';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
} from '@/utils/exportConversation';

import { DeleteConversationConfirmationDialog } from './DeleteConversationConfirmationDialog';
import { ConversationItem, getConversationName } from './ConversationItem';

import { useConversationMetadata } from '@/hooks/useConversationMetadata';
import type { ConversationSummary } from '@/types/conversation';
import type { ExternalSessionCatalogItem } from '@/types/api';
import { type FC, useRef, useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { use$ } from '@legendapp/state/react';
import { type Observable } from '@legendapp/state';
import { conversations$ } from '@/stores/conversations';
import { toast } from 'sonner';

const HARNESS_LABELS: Record<string, string> = {
  'claude-code': 'CC',
  gptme: 'gptme',
  codex: 'Codex',
  copilot: 'Copilot',
};

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

const SHOW_EXTERNAL_KEY = 'gptme:show-external-sessions';

function readShowExternal(): boolean {
  try {
    return localStorage.getItem(SHOW_EXTERNAL_KEY) === 'true';
  } catch {
    return false;
  }
}

function writeShowExternal(value: boolean): void {
  try {
    localStorage.setItem(SHOW_EXTERNAL_KEY, value ? 'true' : 'false');
  } catch {
    // Silently ignore storage errors
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
  externalSessions?: ExternalSessionCatalogItem[];
  onSelectExternal?: (id: string) => void;
  selectedExternalId?: string;
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
  externalSessions,
  onSelectExternal,
  selectedExternalId,
}) => {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);

  const { toggleStar } = useConversationMetadata();

  const [showStarredOnly, setShowStarredOnly] = useState(false);

  const [sortBy, setSortBy] = useState<SortBy>(readSortPreference);
  const useInfiniteScroll = sortBy === 'recent';
  const handleSortChange = (value: SortBy) => {
    setSortBy(value);
    writeSortPreference(value);
  };

  const [showExternal, setShowExternal] = useState(readShowExternal);
  const handleToggleExternal = () => {
    const next = !showExternal;
    writeShowExternal(next);
    setShowExternal(next);
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

  const normalizedFilter = filterQuery.trim().toLowerCase();
  const matchesFilter = (conv: ConversationSummary) =>
    !normalizedFilter || getConversationName(conv).toLowerCase().includes(normalizedFilter);

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

  // Cap external sessions merged into the timeline to avoid unbounded rendering.
  const MAX_EXTERNAL_INLINE = 50;

  // For 'recent' view: merge external sessions into the native list (true unified timeline).
  // External sessions are adapted to ConversationSummary shape and interleaved by modified time.
  // Sessions with no parseable timestamp are excluded from the merged view (no meaningful sort key).
  // For non-recency sorts, external sessions appear in a separate section (no message count to sort by).
  const externalAsSummaries: ConversationSummary[] =
    showExternal && externalSessions && onSelectExternal
      ? externalSessions.slice(0, MAX_EXTERNAL_INLINE).flatMap((item) => {
          const modifiedMs = Date.parse(item.last_activity ?? item.started_at ?? '');
          // Skip sessions with no parseable timestamp — placing them at epoch (Jan 1970) is misleading.
          if (!Number.isFinite(modifiedMs) || modifiedMs <= 0) return [];
          const modified = modifiedMs / 1000;
          const label = HARNESS_LABELS[item.harness] ?? item.harness;
          const name = item.session_name ?? item.session_id.slice(0, 8);
          return [
            {
              id: `ext:${item.id}`,
              name,
              modified,
              _externalSession: { id: item.id, harness: item.harness, label },
            } satisfies ConversationSummary,
          ];
        })
      : [];

  // For 'recent': merge and re-sort so external sessions appear in the right timeline position.
  const sortedConversationsWithExternal =
    sortBy === 'recent' && externalAsSummaries.length > 0
      ? [...sortedRealConversations, ...externalAsSummaries].sort((a, b) => b.modified - a.modified)
      : sortedRealConversations;

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
          {/* Star filter toggle + sort control + external sessions toggle */}
          {(realConversations.length > 0 ||
            (onSelectExternal && externalSessions && externalSessions.length > 0)) && (
            <div className="flex items-center justify-between px-1 pt-1">
              {realConversations.length > 0 ? (
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
              ) : (
                <div />
              )}
              <div className="flex items-center gap-1">
                {realConversations.length > 0 && (
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
                )}
                {onSelectExternal && externalSessions && externalSessions.length > 0 && (
                  <button
                    aria-label={showExternal ? 'Hide external sessions' : 'Show external sessions'}
                    aria-pressed={showExternal}
                    className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs transition-colors ${
                      showExternal
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                    onClick={handleToggleExternal}
                    title="Toggle external sessions (Claude Code, Codex, etc.)"
                  >
                    <Layers className="h-3 w-3" />
                    Ext
                  </button>
                )}
              </div>
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

      {/* Render conversations: grouped by date for 'recent' (native + external merged), flat list for other sorts.
          External sessions render independently of the native loading/error state. */}
      {(!isLoading || externalAsSummaries.length > 0) &&
        !isError &&
        sortBy === 'recent' &&
        groupByDate<ConversationSummary>(
          sortedConversationsWithExternal,
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
              {items.map((conv) => {
                const ext = conv._externalSession;
                if (ext) {
                  // External session: compact badge row, no context menu
                  const isExtSelected = selectedExternalId === ext.id;
                  return (
                    <button
                      key={conv.id}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent/50 ${
                        isExtSelected ? 'bg-accent ring-1 ring-primary' : ''
                      }`}
                      aria-pressed={isExtSelected}
                      onClick={() => onSelectExternal!(ext.id)}
                    >
                      <Badge variant="secondary" className="h-4 flex-shrink-0 px-1.5 text-[10px]">
                        {ext.label}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate text-xs">{conv.name}</span>
                      {conv.modified > 0 && (
                        <span className="flex-shrink-0 text-[10px] text-muted-foreground">
                          {getRelativeTimeString(new Date(conv.modified * 1000))}
                        </span>
                      )}
                    </button>
                  );
                }
                return (
                  <ConversationItem
                    key={conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id}
                    conv={conv}
                    showLabel={showServerLabels}
                    selectedId$={selectedId$}
                    onSelect={onSelect}
                    renamingId={renamingId}
                    renameValue={renameValue}
                    setRenameValue={setRenameValue}
                    onRenameSubmit={handleRenameSubmit}
                    onRenameCancel={handleRenameCancel}
                    setDeleteTarget={setDeleteTarget}
                    getIsStarred={getIsStarred}
                    toggleStar={toggleStar}
                    onExportMarkdown={handleExportMarkdown}
                    onExportJSON={handleExportJSON}
                    onStartRename={handleStartRename}
                    demoIds={demoIds}
                    normalizedFilter={normalizedFilter}
                    onOpenInSplitView={onOpenInSplitView}
                    setOptimisticStars={setOptimisticStars}
                  />
                );
              })}
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
              selectedId$={selectedId$}
              onSelect={onSelect}
              renamingId={renamingId}
              renameValue={renameValue}
              setRenameValue={setRenameValue}
              onRenameSubmit={handleRenameSubmit}
              onRenameCancel={handleRenameCancel}
              setDeleteTarget={setDeleteTarget}
              getIsStarred={getIsStarred}
              toggleStar={toggleStar}
              onExportMarkdown={handleExportMarkdown}
              onExportJSON={handleExportJSON}
              onStartRename={handleStartRename}
              demoIds={demoIds}
              normalizedFilter={normalizedFilter}
              onOpenInSplitView={onOpenInSplitView}
              setOptimisticStars={setOptimisticStars}
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
              <ConversationItem
                key={conv.id}
                conv={conv}
                selectedId$={selectedId$}
                onSelect={onSelect}
                renamingId={renamingId}
                renameValue={renameValue}
                setRenameValue={setRenameValue}
                onRenameSubmit={handleRenameSubmit}
                onRenameCancel={handleRenameCancel}
                setDeleteTarget={setDeleteTarget}
                getIsStarred={getIsStarred}
                toggleStar={toggleStar}
                onExportMarkdown={handleExportMarkdown}
                onExportJSON={handleExportJSON}
                onStartRename={handleStartRename}
                demoIds={demoIds}
                normalizedFilter={normalizedFilter}
                onOpenInSplitView={onOpenInSplitView}
                setOptimisticStars={setOptimisticStars}
              />
            ))}
          </div>
        </div>
      )}

      {/* For non-recency sorts: external sessions can't be ranked by message count or alpha among
          native conversations, so show them in a labeled group at the bottom.
          For 'recent' sort, external sessions are merged inline above (true unified timeline). */}
      {!isLoading &&
        !isError &&
        sortBy !== 'recent' &&
        externalAsSummaries.length > 0 &&
        onSelectExternal && (
          <div>
            <div className="flex items-center gap-1 px-2 py-1.5 text-xs font-medium text-muted-foreground">
              <Layers className="h-3 w-3" />
              External Sessions
            </div>
            <div className="space-y-1 px-2">
              {externalAsSummaries.map((conv) => {
                const ext = conv._externalSession!;
                const isExtSelected = selectedExternalId === ext.id;
                return (
                  <button
                    key={conv.id}
                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent/50 ${
                      isExtSelected ? 'bg-accent ring-1 ring-primary' : ''
                    }`}
                    aria-pressed={isExtSelected}
                    onClick={() => onSelectExternal(ext.id)}
                  >
                    <Badge variant="secondary" className="h-4 flex-shrink-0 px-1.5 text-[10px]">
                      {ext.label}
                    </Badge>
                    <span className="min-w-0 flex-1 truncate text-xs">{conv.name}</span>
                    {conv.modified > 0 && (
                      <span className="flex-shrink-0 text-[10px] text-muted-foreground">
                        {getRelativeTimeString(new Date(conv.modified * 1000))}
                      </span>
                    )}
                  </button>
                );
              })}
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
