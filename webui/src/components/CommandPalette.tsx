import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
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
} from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';
import type { ConversationSummary } from '@/types/conversation';
import { conversations$, selectedConversation$ } from '@/stores/conversations';
import { commandPaletteOpen$ } from '@/stores/commandPalette';
import {
  exportConversationAsMarkdown,
  exportConversationAsJSON,
  getExportableMessages,
} from '@/utils/exportConversation';
import { toast } from 'sonner';
import { settingsModal$ } from '@/stores/settingsModal';

interface CommandAction {
  id: string;
  label: string;
  description?: string;
  icon: React.ReactNode;
  keywords: string[];
  action: () => void;
  group: string;
}

export function CommandPalette() {
  const [open, setOpenState] = useState(false);
  const [search, setSearch] = useState('');
  const [conversationResults, setConversationResults] = useState<ConversationSummary[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const navigate = useNavigate();
  const { api } = useApi();
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

  // Toggle command palette with Cmd+K or Ctrl+K
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpenState((prev) => {
          const next = !prev;
          commandPaletteOpen$.set(next);
          return next;
        });
      }
    };

    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, []);

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
        const results = await api.searchConversations(currentSearch, 10);
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
          navigate('/');
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
          navigate('/agents');
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
          settingsModal$.open.set(true);
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
          navigate('/');
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
          navigate('/workspaces');
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
          navigate('/agents');
          setOpen(false);
        },
        group: 'Navigation',
      },
      // Conversation-specific actions (only when a conversation is selected)
      ...(selectedConversation$.get()
        ? [
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
    [navigate, setOpen]
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
                    navigate(`/chat/${conv.id}`);
                    setOpen(false);
                  }}
                >
                  <MessageSquare className="mr-2 h-4 w-4" />
                  <div className="flex flex-1 flex-col overflow-hidden">
                    <span className="truncate">{stripDatePrefix(conv.name)}</span>
                    <span className="text-xs text-muted-foreground">
                      {conv.messages} messages · {formatRelativeTime(conv.modified)}
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
