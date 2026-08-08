import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from './ui/command';
import {
  Settings,
  Plus,
  FileText,
  Users,
  Sparkles,
  Home,
  MessageSquare,
  Download,
  Clipboard,
} from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import type { ConversationSummary } from '@/types/conversation';
import { use$ } from '@legendapp/state/react';
import { conversations$, selectedConversation$ } from '@/stores/conversations';
import { demoConversations } from '@/democonversations';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import { getClientForServer } from '@/stores/serverClients';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  copyConversationToClipboard,
  getExportableMessages,
} from '@/utils/exportConversation';
import { appRoute, chatRoute } from '@/utils/routes';
import { isDemoMode } from '@/utils/connectionConfig';
import { toast } from 'sonner';

interface CommandAction {
  id: string;
  label: string;
  description?: string;
  icon: React.ReactNode;
  keywords: string[];
  action: () => void;
  group: string;
}

interface CopyTrajectoryOptions {
  includeThinking: boolean;
  includeTools: boolean;
  successMessage: string;
}

export function CommandPalette() {
  const [open, setOpenState] = useState(false);
  const [search, setSearch] = useState('');
  const [conversationResults, setConversationResults] = useState<ConversationSummary[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { api, getClient } = useApi();
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sync open state bidirectionally with the observable (for external control, e.g. MenuBar search button)
  const setOpen = useCallback((value: boolean) => {
    setOpenState(value);
    commandPaletteOpen$.set(value);
  }, []);

  useEffect(() => {
    return commandPaletteOpen$.onChange(({ value }) => {
      setOpenState(value);
    });
  }, []);

  // Toggle command palette with Cmd+K or Ctrl+K; close on Escape.
  // The Escape handler is explicit because cmdk v1 intercepts the key at the
  // element level before Radix UI Dialog's DismissableLayer sees it.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpenState((prev) => {
          const next = !prev;
          commandPaletteOpen$.set(next);
          return next;
        });
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    };

    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, [setOpen]);

  // Alt+N — new conversation (skip when typing in an input)
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== 'KeyN' || !e.altKey || e.metaKey || e.ctrlKey) return;
      const target = e.target as HTMLElement | null;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target?.isContentEditable
      ) {
        return;
      }
      e.preventDefault();
      navigate(appRoute('/'));
      setOpen(false);
    };
    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, [navigate, setOpen]);

  // Reset search when closing
  useEffect(() => {
    if (!open) {
      setSearch('');
      setConversationResults([]);
      setIsSearching(false);
    }
  }, [open]);

  // Debounced conversation search
  useEffect(() => {
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
    }

    if (!search || search.length < 2) {
      setConversationResults([]);
      setIsSearching(false);
      return;
    }

    let cancelled = false;
    setIsSearching(true);
    const currentSearch = search;
    searchTimerRef.current = setTimeout(async () => {
      try {
        const results = await api.searchConversations(currentSearch, 10, true);
        if (!cancelled) {
          setConversationResults(results);
        }
      } catch {
        if (!cancelled) {
          setConversationResults([]);
        }
      } finally {
        if (!cancelled) {
          setIsSearching(false);
        }
      }
    }, 200);

    return () => {
      cancelled = true;
      if (searchTimerRef.current) {
        clearTimeout(searchTimerRef.current);
      }
    };
  }, [search, api]);

  const copyTrajectory = useCallback(
    async ({ includeThinking, includeTools, successMessage }: CopyTrajectoryOptions) => {
      const convId = selectedConversation$.get();
      if (!convId) {
        toast.error('No messages to copy');
        return;
      }

      try {
        const demoConversation = demoConversations.find(({ id }) => id === convId);
        let fullData;
        if (isDemoMode() && demoConversation) {
          fullData = conversations$.get(convId)?.data.peek();
        } else {
          const serverId = new URLSearchParams(location.search).get('server');
          if (serverId && !getClientForServer(serverId)) {
            toast.error('Server not found');
            return;
          }
          fullData = await (serverId ? getClient(serverId) : api).getConversation(convId);
        }
        if (!fullData) {
          toast.error('No messages to copy');
          return;
        }
        if (!fullData.log.length) {
          toast.error('No messages to copy');
          return;
        }
        await copyConversationToClipboard(fullData.name || convId, fullData.log, {
          includeThinking,
          includeTools,
        });
        toast.success(successMessage);
        setOpen(false);
      } catch {
        toast.error('Failed to copy to clipboard');
      }
    },
    [api, getClient, location.search, setOpen]
  );

  // Track selected conversation reactively so the actions memo recomputes when it changes.
  // Without this, the copy-trajectory commands won't appear after navigating to a
  // conversation post-mount (memo stays stale because selectedConversation$ is not a dep).
  const selectedConvId = use$(selectedConversation$);

  // Define available actions
  const actions = useMemo<CommandAction[]>(
    () => [
      {
        id: 'new-conversation',
        label: 'New Conversation',
        description: 'Start a new chat',
        icon: <Plus className="mr-2 h-4 w-4" />,
        keywords: ['new', 'chat', 'conversation', 'create'],
        action: () => {
          navigate(appRoute('/'));
          setOpen(false);
        },
        group: 'Actions',
      },
      {
        id: 'create-agent',
        label: 'Create Agent',
        description: 'Set up a new AI agent',
        icon: <Sparkles className="mr-2 h-4 w-4" />,
        keywords: ['agent', 'create', 'new', 'ai'],
        action: () => {
          navigate(appRoute('/agents'));
          setOpen(false);
        },
        group: 'Actions',
      },
      {
        id: 'settings',
        label: 'Settings',
        description: 'Configure application',
        icon: <Settings className="mr-2 h-4 w-4" />,
        keywords: ['settings', 'preferences', 'config'],
        action: () => {
          setOpen(false);
          navigate(appRoute('/settings'));
        },
        group: 'Navigation',
      },
      {
        id: 'home',
        label: 'Home',
        description: 'Go to home page',
        icon: <Home className="mr-2 h-4 w-4" />,
        keywords: ['home', 'main'],
        action: () => {
          navigate(appRoute('/'));
          setOpen(false);
        },
        group: 'Navigation',
      },
      {
        id: 'workspaces',
        label: 'Workspaces',
        description: 'Manage workspaces',
        icon: <FileText className="mr-2 h-4 w-4" />,
        keywords: ['workspace', 'folder', 'project'],
        action: () => {
          navigate(appRoute('/workspaces'));
          setOpen(false);
        },
        group: 'Navigation',
      },
      {
        id: 'agents',
        label: 'Agents',
        description: 'View all agents',
        icon: <Users className="mr-2 h-4 w-4" />,
        keywords: ['agents', 'list', 'view'],
        action: () => {
          navigate(appRoute('/agents'));
          setOpen(false);
        },
        group: 'Navigation',
      },
      // Conversation-specific actions (only when a conversation is selected)
      ...(selectedConvId
        ? [
            {
              id: 'copy-trajectory-markdown',
              label: 'Copy trajectory as Markdown',
              description: 'Copy whole conversation without thinking or tool output',
              icon: <Clipboard className="mr-2 h-4 w-4" />,
              keywords: ['copy', 'clipboard', 'trajectory', 'markdown', 'transcript', 'share'],
              action: () =>
                copyTrajectory({
                  includeThinking: false,
                  includeTools: false,
                  successMessage: 'Trajectory copied to clipboard',
                }),
              group: 'Conversation',
            },
            {
              id: 'copy-trajectory-markdown-full',
              label: 'Copy trajectory as Markdown (full)',
              description: 'Copy whole conversation including thinking and tool output',
              icon: <Clipboard className="mr-2 h-4 w-4" />,
              keywords: [
                'copy',
                'clipboard',
                'trajectory',
                'markdown',
                'full',
                'tools',
                'thinking',
              ],
              action: () =>
                copyTrajectory({
                  includeThinking: true,
                  includeTools: true,
                  successMessage: 'Full trajectory copied to clipboard',
                }),
              group: 'Conversation',
            },
            {
              id: 'export-markdown',
              label: 'Export as Markdown',
              description: 'Download current conversation as .md',
              icon: <Download className="mr-2 h-4 w-4" />,
              keywords: ['export', 'download', 'markdown', 'save', 'share'],
              action: () => {
                const convId = selectedConversation$.get();
                const conv = convId ? conversations$.get(convId)?.get() : null;
                if (!conv?.data?.log?.length) {
                  toast.error('No messages to export');
                  return;
                }
                const exportableMessages = getExportableMessages(conv.data.log);
                if (!exportableMessages.length) {
                  toast.error('No visible messages to export');
                  return;
                }
                exportConversationAsMarkdown(
                  convId!,
                  conv.data.name || convId!,
                  exportableMessages
                );
                toast.success('Exported as Markdown');
                setOpen(false);
              },
              group: 'Conversation',
            },
            {
              id: 'export-json',
              label: 'Export as JSON',
              description: 'Download current conversation as .json',
              icon: <Download className="mr-2 h-4 w-4" />,
              keywords: ['export', 'download', 'json', 'save', 'data'],
              action: () => {
                const convId = selectedConversation$.get();
                const conv = convId ? conversations$.get(convId)?.get() : null;
                if (!conv?.data?.log?.length) {
                  toast.error('No messages to export');
                  return;
                }
                exportConversationAsJSON(convId!, conv.data.name || convId!, conv.data.log);
                toast.success('Exported as JSON');
                setOpen(false);
              },
              group: 'Conversation',
            },
          ]
        : []),
    ],
    [navigate, setOpen, copyTrajectory, selectedConvId]
  );

  // Filter actions based on search query
  const filteredActions = useMemo(() => {
    if (!search) return actions;

    const searchLower = search.toLowerCase();
    return actions.filter((action) => {
      if (action.label.toLowerCase().includes(searchLower)) return true;
      if (action.description?.toLowerCase().includes(searchLower)) return true;
      return action.keywords.some((keyword) => keyword.toLowerCase().includes(searchLower));
    });
  }, [search, actions]);

  // Group filtered actions by category
  const groupedActions = useMemo(() => {
    const groups = new Map<string, CommandAction[]>();
    filteredActions.forEach((action) => {
      const group = groups.get(action.group) || [];
      group.push(action);
      groups.set(action.group, group);
    });
    return Array.from(groups.entries());
  }, [filteredActions]);

  // Handle action execution
  const handleSelect = useCallback((action: CommandAction) => {
    action.action();
  }, []);

  // Format relative time for conversation results
  const formatRelativeTime = useCallback((timestamp: number) => {
    const now = Date.now() / 1000;
    const diff = now - timestamp;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return new Date(timestamp * 1000).toLocaleDateString();
  }, []);

  // Strip leading date prefix from conversation name (matches ConversationList behavior)
  const stripDatePrefix = useCallback((name: string) => {
    return name.replace(/^\d{4}-\d{2}-\d{2}[- ]?/, '');
  }, []);

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput
        placeholder="Type a command or search conversations..."
        value={search}
        onValueChange={setSearch}
      />
      <CommandList>
        <CommandEmpty>{isSearching ? 'Searching...' : 'No results found.'}</CommandEmpty>

        {/* Conversation search results */}
        {conversationResults.length > 0 && (
          <>
            <CommandGroup heading="Conversations">
              {conversationResults.map((conv) => (
                <CommandItem
                  key={`conv-${conv.id}`}
                  value={`conv-${conv.id} ${conv.name}`}
                  onSelect={() => {
                    navigate(chatRoute(conv.id));
                    setOpen(false);
                  }}
                >
                  <MessageSquare className="mr-2 h-4 w-4" />
                  <div className="flex flex-1 flex-col overflow-hidden">
                    <span className="truncate">{stripDatePrefix(conv.name)}</span>
                    <span className="text-xs text-muted-foreground">
                      {conv.messages ?? 0} messages · {formatRelativeTime(conv.modified)}
                    </span>
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
            {groupedActions.length > 0 && <CommandSeparator />}
          </>
        )}

        {/* Static actions */}
        {groupedActions.map(([groupName, groupActions], index) => (
          <div key={groupName}>
            {index > 0 && <CommandSeparator />}
            <CommandGroup heading={groupName}>
              {groupActions.map((action) => (
                <CommandItem
                  key={action.id}
                  value={action.id}
                  onSelect={() => handleSelect(action)}
                >
                  {action.icon}
                  <div className="flex flex-col">
                    <span>{action.label}</span>
                    {action.description && (
                      <span className="text-xs text-muted-foreground">{action.description}</span>
                    )}
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          </div>
        ))}
      </CommandList>
    </CommandDialog>
  );
}
