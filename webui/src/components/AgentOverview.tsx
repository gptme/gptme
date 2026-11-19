import { useMemo, type FC } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Bot, ExternalLink, MessageSquare } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { Button } from '@/components/ui/button';
import type { ConversationSummary } from '@/types/conversation';
import { extractAgentsFromConversations, matchesAgent } from '@/utils/workspaceUtils';
import { agentConversationsRoute, appRoute, chatRoute } from '@/utils/routes';
import { serverRegistry$ } from '@/stores/servers';

interface Props {
  conversations: ConversationSummary[];
  isLoading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
}

export const AgentOverview: FC<Props> = ({ conversations, isLoading, hasMore, onLoadMore }) => {
  const [params] = useSearchParams();
  const path = params.get('path');
  const serverId = params.get('server') || undefined;
  const agent = useMemo(
    () =>
      extractAgentsFromConversations(conversations).find(
        (item) => item.path === path && item.serverId === serverId
      ),
    [conversations, path, serverId]
  );
  const recent = useMemo(
    () =>
      agent
        ? conversations
            .filter((item) => matchesAgent(item, agent))
            .sort((a, b) => b.modified - a.modified)
        : [],
    [conversations, agent]
  );
  const conversationUrl = (id: string) => {
    const query = new URLSearchParams(new URL(appRoute('/'), window.location.origin).search);
    if (serverId) query.set('server', serverId);
    return chatRoute(id, query.toString());
  };
  const server = serverRegistry$.get().servers.find((item) => item.id === serverId);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <Link
          to={appRoute('/agents')}
          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> All agents
        </Link>
        {!agent ? (
          <div className="rounded-lg border bg-card p-6">
            <h1 className="text-xl font-semibold">
              {isLoading ? 'Loading agent…' : 'Agent not found in loaded conversations'}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Connect to the owning server or load more conversations. This link never falls back to
              another agent with the same name.
            </p>
          </div>
        ) : (
          <>
            <header className="rounded-lg border bg-card p-6">
              <div className="flex items-start gap-4">
                {agent.hasAvatar && server ? (
                  <img
                    src={`${server.baseUrl.replace(/\/+$/, '')}/api/v2/agents/avatar?path=${encodeURIComponent(agent.path)}`}
                    alt=""
                    className="h-12 w-12 rounded-full object-cover"
                  />
                ) : (
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-muted">
                    <Bot className="h-6 w-6" />
                  </div>
                )}
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Agent overview
                  </p>
                  <h1 className="break-words text-2xl font-semibold">{agent.name}</h1>
                  <p className="mt-1 break-all font-mono text-sm text-muted-foreground">
                    {agent.path}
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Server: {agent.serverName || server?.name || serverId || 'Demo'}
                  </p>
                </div>
              </div>
              <div className="mt-5 flex flex-wrap gap-2">
                <Button asChild>
                  <Link to={agentConversationsRoute(agent)}>
                    <MessageSquare className="mr-2 h-4 w-4" />
                    View conversations
                  </Link>
                </Button>
                {Object.entries(agent.urls ?? {})
                  .filter(([, url]) => /^https?:\/\//i.test(url))
                  .map(([label, url]) => (
                    <Button key={label} variant="outline" asChild>
                      <a href={url} target="_blank" rel="noopener noreferrer">
                        <ExternalLink className="mr-2 h-4 w-4" />
                        {label}
                      </a>
                    </Button>
                  ))}
              </div>
            </header>
            <section aria-labelledby="agent-activity" className="rounded-lg border bg-card p-5">
              <h2 id="agent-activity" className="font-medium">
                Activity
              </h2>
              <dl className="mt-3 grid gap-4 sm:grid-cols-2">
                <div>
                  <dt className="text-sm text-muted-foreground">Loaded conversations</dt>
                  <dd className="text-xl font-semibold">{recent.length}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">Last conversation update</dt>
                  <dd className="mt-1 text-sm">
                    <time
                      dateTime={agent.lastUsed}
                      title={new Date(agent.lastUsed).toLocaleString()}
                    >
                      {formatDistanceToNow(new Date(agent.lastUsed), { addSuffix: true })}
                    </time>
                  </dd>
                </div>
              </dl>
              <p className="mt-4 text-xs text-muted-foreground">
                Runtime status is not available from conversation history. A recent update does not
                mean the agent is running.
              </p>
            </section>
            <section aria-labelledby="agent-conversations">
              <h2 id="agent-conversations" className="mb-3 font-medium">
                Recent conversations
              </h2>
              <div className="divide-y rounded-lg border bg-card">
                {recent.slice(0, 5).map((conversation) => (
                  <Link
                    key={conversation.id}
                    to={conversationUrl(conversation.id)}
                    className="block p-4 transition-colors hover:bg-accent/50"
                  >
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="truncate text-sm font-medium">{conversation.name}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {formatDistanceToNow(new Date(conversation.modified * 1000), {
                          addSuffix: true,
                        })}
                      </span>
                    </div>
                    {conversation.last_message_preview && (
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                        {conversation.last_message_preview}
                      </p>
                    )}
                  </Link>
                ))}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                Based on loaded conversation history; older conversations may not be loaded yet.
              </p>
            </section>
          </>
        )}
        {hasMore && (
          <Button variant="outline" disabled={isLoading} onClick={onLoadMore}>
            Load more conversations
          </Button>
        )}
      </div>
    </div>
  );
};
