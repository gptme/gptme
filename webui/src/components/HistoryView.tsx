import { type FC, useMemo, useState, useCallback, useRef, useEffect } from 'react';
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
  Loader2,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useApi } from '@/contexts/ApiContext';
import { ActivityCalendar } from '@/components/ActivityCalendar';
import { getRelativeTimeString, toISODate } from '@/utils/time';
import type { ConversationSummary } from '@/types/conversation';

const PAGE_SIZE = 50;

/** Fetch all conversation summaries for calendar/stats */
function useAllConversations() {
  const { api, connectionConfig } = useApi();
  const isConnected = use$(api.isConnected$);

  return useQuery({
    queryKey: ['conversations-all', connectionConfig.baseUrl, isConnected],
    queryFn: async () => {
      const result = await api.getConversationsPaginated(0, 100000);
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

const ConversationRow: FC<{
  conv: ConversationSummary;
  onClick: (conv: ConversationSummary) => void;
}> = ({ conv, onClick }) => (
  <div
    className="flex cursor-pointer items-start gap-3 border-b px-4 py-3 last:border-b-0 hover:bg-accent/50"
    onClick={() => onClick(conv)}
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
          <TooltipContent>{new Date(conv.modified * 1000).toLocaleString()}</TooltipContent>
        </Tooltip>
      </div>
      {conv.last_message_preview && (
        <p className="mt-0.5 truncate text-sm text-muted-foreground">
          {conv.last_message_role === 'user' ? '\u2192 ' : '\u2190 '}
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
);

export const HistoryView: FC = () => {
  const navigate = useNavigate();
  const { data: conversations = [], isLoading } = useAllConversations();
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [selectedYear, setSelectedYear] = useState<number | null>(null); // null = current year view
  const sentinelRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Build activity map from conversations
  const activityData = useMemo(() => {
    const map = new Map<string, number>();
    for (const conv of conversations) {
      const date = toISODate(new Date(conv.modified * 1000));
      map.set(date, (map.get(date) || 0) + 1);
    }
    return map;
  }, [conversations]);

  // Determine available years from data
  const availableYears = useMemo(() => {
    const years = new Set<number>();
    const currentYear = new Date().getFullYear();
    years.add(currentYear);
    for (const conv of conversations) {
      years.add(new Date(conv.modified * 1000).getFullYear());
    }
    return [...years].sort((a, b) => b - a);
  }, [conversations]);

  // Calendar date range based on selected year
  // null = "Last year" (52 weeks ending today), otherwise Jan 1–Dec 31 of that year
  const calendarRange = useMemo(() => {
    if (selectedYear === null) {
      return { startDate: undefined, endDate: undefined }; // defaults in calendar: 52 weeks ending today
    }
    const currentYear = new Date().getFullYear();
    return {
      startDate: new Date(selectedYear, 0, 1),
      endDate: selectedYear === currentYear ? undefined : new Date(selectedYear, 11, 31),
    };
  }, [selectedYear]);

  const displayLabel = selectedYear === null ? 'Past 12 months' : String(selectedYear);

  // Compute stats (scoped to selected year if not current)
  const stats = useMemo(() => {
    const totalConversations = conversations.length;
    const totalMessages = conversations.reduce((sum, c) => sum + c.messages, 0);
    const activeDays = activityData.size;

    // Current streak
    let streak = 0;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const checkDate = new Date(today);
    if (!activityData.has(toISODate(checkDate))) {
      checkDate.setDate(checkDate.getDate() - 1);
    }
    while (activityData.has(toISODate(checkDate))) {
      streak++;
      checkDate.setDate(checkDate.getDate() - 1);
    }

    return { totalConversations, totalMessages, activeDays, streak };
  }, [conversations, activityData]);

  // Scoped stats for the calendar header
  const calendarStats = useMemo(() => {
    if (selectedYear === null) {
      // "Last year" = all activity in the 52-week window
      const end = new Date();
      end.setHours(12, 0, 0, 0);
      const start = new Date(end);
      start.setDate(end.getDate() - 52 * 7);
      const startStr = toISODate(start);
      const endStr = toISODate(end);
      let count = 0;
      for (const [date, n] of activityData) {
        if (date >= startStr && date <= endStr) count += n;
      }
      return count;
    }
    let count = 0;
    for (const [date, n] of activityData) {
      if (date.startsWith(String(selectedYear))) count += n;
    }
    return count;
  }, [activityData, selectedYear]);

  // Filter conversations
  const filteredConversations = useMemo(() => {
    let filtered = conversations;

    if (selectedDate) {
      filtered = filtered.filter((conv) => {
        const convDate = toISODate(new Date(conv.modified * 1000));
        return convDate === selectedDate;
      });
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (conv) =>
          conv.name.toLowerCase().includes(q) ||
          conv.id.toLowerCase().includes(q) ||
          conv.last_message_preview?.toLowerCase().includes(q)
      );
    }

    const sorted = filtered === conversations ? [...filtered] : filtered;
    sorted.sort((a, b) => b.modified - a.modified);
    return sorted;
  }, [conversations, selectedDate, searchQuery]);

  // Reset visible count when filters change
  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [selectedDate, searchQuery]);

  // Intersection observer for infinite scroll
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const container = scrollRef.current;
    if (!sentinel || !container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisibleCount((prev) => prev + PAGE_SIZE);
        }
      },
      { root: container, rootMargin: '200px' }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [filteredConversations]);

  const visibleConversations = filteredConversations.slice(0, visibleCount);
  const hasMore = visibleCount < filteredConversations.length;

  const handleDayClick = useCallback((date: string) => {
    setSelectedDate((prev) => (prev === date ? null : date));
  }, []);

  const handleConversationClick = useCallback(
    (conv: ConversationSummary) => {
      navigate(`/chat/${conv.id}`);
    },
    [navigate]
  );

  const handleYearPrev = useCallback(() => {
    const minYear = availableYears[availableYears.length - 1] ?? new Date().getFullYear();
    const currentYear = new Date().getFullYear();
    setSelectedYear((prev) => {
      if (prev === null) return currentYear; // "Last year" → current year
      return Math.max(prev - 1, minYear);
    });
  }, [availableYears]);

  const handleYearNext = useCallback(() => {
    const currentYear = new Date().getFullYear();
    setSelectedYear((prev) => {
      if (prev === null) return null; // already at latest
      if (prev >= currentYear) return null; // go to "Last year"
      return prev + 1;
    });
  }, []);

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
              value={stats.totalConversations.toLocaleString()}
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
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-medium text-muted-foreground">
                {calendarStats.toLocaleString()} conversations in{' '}
                {selectedYear === null ? 'the past 12 months' : selectedYear}
              </h2>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleYearPrev}
                  disabled={
                    selectedYear !== null &&
                    selectedYear <= (availableYears[availableYears.length - 1] ?? selectedYear)
                  }
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="min-w-[4.5rem] text-center text-sm font-medium">
                  {displayLabel}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleYearNext}
                  disabled={selectedYear === null}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
            {isLoading ? (
              <div className="flex h-[120px] items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading activity data...
              </div>
            ) : (
              <ActivityCalendar
                activityData={activityData}
                startDate={calendarRange.startDate}
                endDate={calendarRange.endDate}
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
                  {filteredConversations.length.toLocaleString()} conversation
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
            <div ref={scrollRef} className="max-h-[600px] overflow-y-auto">
              {isLoading ? (
                <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Loading conversations...
                </div>
              ) : filteredConversations.length === 0 ? (
                <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
                  {searchQuery || selectedDate
                    ? 'No conversations match your filters.'
                    : 'No conversations yet.'}
                </div>
              ) : (
                <>
                  {visibleConversations.map((conv) => (
                    <ConversationRow
                      key={conv.serverId ? `${conv.serverId}:${conv.id}` : conv.id}
                      conv={conv}
                      onClick={handleConversationClick}
                    />
                  ))}
                  {hasMore && (
                    <div
                      ref={sentinelRef}
                      className="flex items-center justify-center p-4 text-sm text-muted-foreground"
                    >
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Loading more...
                    </div>
                  )}
                  {!hasMore && filteredConversations.length > PAGE_SIZE && (
                    <div className="py-3 text-center text-xs text-muted-foreground">
                      All {filteredConversations.length.toLocaleString()} conversations loaded
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
