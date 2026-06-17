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
  Columns2,
  Star,
} from 'lucide-react';
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
import { getRelativeTimeString } from '@/utils/time';
import { computeConversationCost, formatCost, formatTokens } from '@/utils/conversationCost';
import { demoConversations, getDemoMessages } from '@/democonversations';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
} from '@/utils/exportConversation';
import type { MessageRole, ConversationSummary } from '@/types/conversation';
import { type FC, memo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Computed } from '@legendapp/state/react';
import { type Observable } from '@legendapp/state';
import { conversations$ } from '@/stores/conversations';

type MessageBreakdown = Partial<Record<MessageRole, number>>;

/** strip leading YYYY-MM-DD from name if present */
export function stripDate(name: string): string {
  const match = name.match(/^\d{4}-\d{2}-\d{2}[- ](.*)/);
  return match ? match[1] : name;
}

export function getConversationName(conv: ConversationSummary): string {
  const storeConv = conversations$.get(conv.id)?.get();
  return storeConv?.data?.name || conv.name || stripDate(conv.id);
}

export function highlightText(text: string, query: string): React.ReactNode {
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

export interface ConversationItemProps {
  conv: ConversationSummary;
  showLabel?: boolean;
  selectedId$?: Observable<string | null>;
  onSelect: (id: string, serverId?: string) => void;
  renamingId: string | null;
  renameValue: string;
  setRenameValue: (value: string) => void;
  onRenameSubmit: (id: string) => Promise<void>;
  onRenameCancel: () => void;

  setDeleteTarget: (target: { id: string; name: string } | null) => void;
  getIsStarred: (conv: ConversationSummary) => boolean;
  toggleStar: (id: string, currentlyStarred: boolean) => Promise<boolean>;
  onExportMarkdown: (conv: ConversationSummary) => void;
  onExportJSON: (conv: ConversationSummary) => void;
  onStartRename: (conv: ConversationSummary) => void;
  demoIds: Set<string>;
  normalizedFilter: string;
  onOpenInSplitView?: (id: string) => void;
  setOptimisticStars: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
}

const ConversationItemInner: FC<ConversationItemProps> = ({
  conv,
  showLabel,
  selectedId$,
  onSelect,
  renamingId,
  renameValue,
  setRenameValue,
  onRenameSubmit,
  onRenameCancel,
  setDeleteTarget,
  getIsStarred,
  toggleStar,
  onExportMarkdown,
  onExportJSON,
  onStartRename,
  demoIds,
  normalizedFilter,
  onOpenInSplitView,
  setOptimisticStars,
}) => {
  const queryClient = useQueryClient();
  const demoConv = demoConversations.find((dc) => dc.id === conv.id);
  const isDemo = !!demoConv;
  const isRenaming = renamingId === conv.id;

  const getMessageBreakdown = (): MessageBreakdown => {
    if (demoConv) {
      const messages = getDemoMessages(demoConv.id);
      return messages.reduce((acc, msg) => {
        acc[msg.role] = (acc[msg.role] || 0) + 1;
        return acc;
      }, {} as MessageBreakdown);
    }
    const storeConv = conversations$.get(conv.id)?.get();
    if (!storeConv?.data?.log) return {};
    return storeConv.data.log.reduce((acc, msg$) => {
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
            role="button"
            tabIndex={0}
            aria-pressed={isSelected}
            className={`group cursor-pointer rounded-lg py-2 pl-2 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${
              isSelected ? 'bg-accent' : ''
            }`}
            onClick={() => onSelect(conv.id, conv.serverId)}
            onKeyDown={(e) => {
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
                      onRenameSubmit(conv.id);
                    } else if (e.key === 'Escape') {
                      onRenameCancel();
                    }
                  }}
                  onBlur={() => onRenameSubmit(conv.id)}
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
                        setOptimisticStars((prev) => ({ ...prev, [conv.id]: newVal }));
                        await toggleStar(conv.id, getIsStarred(conv));
                        await queryClient.invalidateQueries({ queryKey: ['conversations'] });
                        setOptimisticStars((prev) => {
                          const next = { ...prev };
                          delete next[conv.id];
                          return next;
                        });
                      }}
                      title={getIsStarred(conv) ? 'Unstar conversation' : 'Star conversation'}
                      aria-label={getIsStarred(conv) ? 'Unstar conversation' : 'Star conversation'}
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
                  <Computed>
                    {() => {
                      const storeConv = conversations$.get(conv.id)?.get();
                      const isLoaded = storeConv?.data?.log?.length > 0;
                      const breakdown = isLoaded ? getMessageBreakdown() : {};
                      const count = isLoaded
                        ? Object.values(breakdown).reduce((a, b) => a + b, 0)
                        : conv.messages;
                      if (!count) return null;
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
                  <TooltipContent>{new Date(conv.modified * 1000).toLocaleString()}</TooltipContent>
                </Tooltip>
                <Computed>
                  {() => {
                    const storeConv = conversations$.get(conv.id)?.get();
                    const isLoaded = storeConv?.data?.log?.length > 0;
                    if (isLoaded) {
                      const cost = computeConversationCost(storeConv!.data!.log);
                      if (cost.hasData) return null;
                    }
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
                  }}
                </Computed>
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
            onStartRename(conv);
          }}
        >
          <Pencil className="mr-2 h-4 w-4" /> Rename
        </ContextMenuItem>
        <ContextMenuSub>
          <ContextMenuSubTrigger>
            <Download className="mr-2 h-4 w-4" /> Export
          </ContextMenuSubTrigger>
          <ContextMenuSubContent>
            <ContextMenuItem onClick={() => onExportMarkdown(conv)}>
              <FileText className="mr-2 h-4 w-4" /> Markdown
            </ContextMenuItem>
            <ContextMenuItem onClick={() => onExportJSON(conv)}>
              <FileJson className="mr-2 h-4 w-4" /> JSON
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
            <Columns2 className="mr-2 h-4 w-4" /> Open in split view
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
            const storeConv = conversations$.get(conv.id)?.get();
            const name = storeConv?.data?.name || conv.name || conv.id;
            setDeleteTarget({ id: conv.id, name });
          }}
        >
          <Trash2 className="mr-2 h-4 w-4" /> Delete
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
};

export const ConversationItem = memo(ConversationItemInner);
