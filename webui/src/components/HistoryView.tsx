import { type FC, useMemo, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { use$ } from '@legendapp/state/react';
import {
  Clock,
  MessageSquare,
  Calendar,
  TrendingUp,
  Flame,
  Search,
  ArrowLeft,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useApi } from '@/contexts/ApiContext';
import { ActivityCalendar } from '@/components/ActivityCalendar';
import { getRelativeTimeString } from '@/utils/time';
import type { ConversationSummary } from '@/types/conversation';

function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Fetch all conversations (high limit) for history overview */
function useAllConversations() {
  const { api, connectionConfig } = useApi();
  const isConnected = use$(api.isConnected$);

  return useQuery({
    queryKey: ['conversations-all', connectionConfig.baseUrl, isConnected],
    queryFn: async () => {
      // Fetch a large number to get full history for the calendar
      const result = await api.getConversationsPaginated(0, 10000);
      return result.conversations;
    },
    enabled: isConnected,
    staleTime: 60 * 1000,
    gcTime: 5 * 60 * 1000,
  });
}

interface StatsCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
}

const StatsCard: FC<StatsCardProps> = ({ icon, label, value }) => (
  <div className="flex items-center gap-3 rounded-lg border bg-card p-4">
    <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md bg-muted">
      {icon}
    </div>
    <div>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-sm text-muted-foreground">{label}</div>
    </div>
  </div>
);

export const HistoryView: FC = () => {
  const navigate = useNavigate();
  const { data: conversations = [], isLoading } = useAllConversations();
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  // Build activity map from conversations
  const activityData = useMemo(() => {
    const map = new Map<string, number>();
    for (const conv of conversations) {
      const date = toISODate(new Date(conv.modified * 1000));
      map.set(date, (map.get(date) || 0) + 1);
    }
    return map;
  }, [conversations]);

  // Compute stats
  const stats = useMemo(() => {
    const totalConversations = conversations.length;
    const totalMessages = conversations.reduce((sum, c) => sum + c.messages, 0);
    const activeDays = activityData.size;

    // Current streak
    let streak = 0;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const checkDate = new Date(today);
    // If no activity today, check if yesterday had activity (allow 1-day gap)
    if (!activityData.has(toISODate(checkDate))) {
      checkDate.setDate(checkDate.getDate() - 1);
    }
    while (activityData.has(toISODate(checkDate))) {
      streak++;
      checkDate.setDate(checkDate.getDate() - 1);
    }

    return { totalConversations, totalMessages, activeDays, streak };
  }, [conversations, activityData]);

  // Filter conversations
  const filteredConversations = useMemo(() => {
    let filtered = [...conversations];

    // Filter by selected date
    if (selectedDate) {
      filtered = filtered.filter((conv) => {
        const convDate = toISODate(new Date(conv.modified * 1000));
        return convDate === selectedDate;
      });
    }

    // Filter by search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (conv) =>
          conv.name.toLowerCase().includes(q) ||
          conv.id.toLowerCase().includes(q) ||
          conv.last_message_preview?.toLowerCase().includes(q)
      );
    }

    // Sort by most recent
    filtered.sort((a, b) => b.modified - a.modified);

    return filtered;
  }, [conversations, selectedDate, searchQuery]);

  const handleDayClick = useCallback((date: string) => {
    setSelectedDate((prev) => (prev === date ? null : date));
  }, []);

  const handleConversationClick = useCallback(
    (conv: ConversationSummary) => {
      navigate(`/chat/${conv.id}`);
    },
    [navigate]
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b px-6 py-4">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={() => navigate('/chat')} className="h-8 w-8">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-xl font-semibold">History</h1>
            <p className="text-sm text-muted-foreground">Browse your past conversations</p>
          </div>
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-6 p-6">
          {/* Stats */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatsCard
              icon={<MessageSquare className="h-5 w-5 text-muted-foreground" />}
              label="Conversations"
              value={stats.totalConversations}
            />
            <StatsCard
              icon={<TrendingUp className="h-5 w-5 text-muted-foreground" />}
              label="Messages"
              value={stats.totalMessages.toLocaleString()}
            />
            <StatsCard
              icon={<Calendar className="h-5 w-5 text-muted-foreground" />}
              label="Active days"
              value={stats.activeDays}
            />
            <StatsCard
              icon={<Flame className="h-5 w-5 text-muted-foreground" />}
              label="Day streak"
              value={stats.streak}
            />
          </div>

          {/* Activity Calendar */}
          <div className="rounded-lg border bg-card p-4">
            <h2 className="mb-3 text-sm font-medium text-muted-foreground">Activity</h2>
            {isLoading ? (
              <div className="flex h-[120px] items-center justify-center text-sm text-muted-foreground">
                Loading activity data...
              </div>
            ) : (
              <ActivityCalendar
                activityData={activityData}
                onDayClick={handleDayClick}
                selectedDate={selectedDate}
              />
            )}
          </div>

          {/* Conversation list */}
          <div className="rounded-lg border bg-card">
            <div className="flex items-center gap-2 border-b px-4 py-3">
              <h2 className="text-sm font-medium text-muted-foreground">
                {selectedDate
                  ? `Conversations on ${new Date(selectedDate + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}`
                  : 'All conversations'}
              </h2>
              {selectedDate && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  onClick={() => setSelectedDate(null)}
                >
                  <X className="mr-1 h-3 w-3" />
                  Clear filter
                </Button>
              )}
              <div className="ml-auto flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {filteredConversations.length} conversation
                  {filteredConversations.length !== 1 ? 's' : ''}
                </span>
              </div>
            </div>

            {/* Search */}
            <div className="border-b px-4 py-2">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search conversations..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-8 pl-8"
                />
              </div>
            </div>

            {/* List */}
            <div className="max-h-[600px] overflow-y-auto">
              {isLoading ? (
                <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
                  Loading conversations...
                </div>
              ) : filteredConversations.length === 0 ? (
                <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
                  {searchQuery || selectedDate
                    ? 'No conversations match your filters.'
                    : 'No conversations yet.'}
                </div>
              ) : (
                filteredConversations.map((conv) => (
                  <div
                    key={conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id}
                    className="flex cursor-pointer items-start gap-3 border-b px-4 py-3 last:border-b-0 hover:bg-accent/50"
                    onClick={() => handleConversationClick(conv)}
                  >
                    <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md bg-muted">
                      <MessageSquare className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="truncate font-medium">{conv.name || conv.id}</span>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="flex-shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                              <Clock className="mr-1 inline h-3 w-3" />
                              {getRelativeTimeString(new Date(conv.modified * 1000))}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            {new Date(conv.modified * 1000).toLocaleString()}
                          </TooltipContent>
                        </Tooltip>
                      </div>
                      {conv.last_message_preview && (
                        <p className="mt-0.5 truncate text-sm text-muted-foreground">
                          {conv.last_message_role === 'user' ? '→ ' : '← '}
                          {conv.last_message_preview}
                        </p>
                      )}
                      <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <MessageSquare className="h-3 w-3" />
                          {conv.messages} messages
                        </span>
                        {conv.workspace && conv.workspace !== '.' && (
                          <span className="truncate">{conv.workspace}</span>
                        )}
                        {conv.agent_name && (
                          <span className="rounded bg-muted px-1.5 py-0.5">{conv.agent_name}</span>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
