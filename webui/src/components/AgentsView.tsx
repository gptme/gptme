import { useState } from 'react';
import { Bot, ExternalLink, Plus, MessageSquare } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { extractAgentsFromConversations } from '@/utils/workspaceUtils';
import { formatDistanceToNow } from 'date-fns';
import type { ConversationSummary } from '@/types/conversation';
import { useApi } from '@/contexts/ApiContext';
import { useNavigate } from 'react-router-dom';
import { selectedAgent$ } from '@/stores/sidebar';
import CreateAgentDialog, { type CreateAgentRequest } from './CreateAgentDialog';
import type { FC } from 'react';

interface AgentsViewProps {
  conversations: ConversationSummary[];
}

export const AgentsView: FC<AgentsViewProps> = ({ conversations }) => {
  const { api, connectionConfig } = useApi();
  const baseUrl = connectionConfig.baseUrl.replace(/\/+$/, '');
  const navigate = useNavigate();
  const agents = extractAgentsFromConversations(conversations);
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const handleAgentClick = (agentName: string) => {
    const agent = agents.find((a) => a.name === agentName);
    if (agent) {
      selectedAgent$.set(agent);
      navigate('/chat');
    }
  };

  const handleAgentCreated = async (agentData: CreateAgentRequest) => {
    try {
      return await api.createAgent(agentData);
    } catch (error) {
      console.error('Failed to create agent:', error);
      throw error;
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Agents</h1>
            <p className="text-sm text-muted-foreground">
              {agents.length} agent{agents.length !== 1 ? 's' : ''} discovered from your
              conversations
            </p>
          </div>
          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Create Agent
          </Button>
        </div>

        {agents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Bot className="mb-4 h-16 w-16 text-muted-foreground opacity-40" />
            <h2 className="mb-2 text-lg font-medium">No agents yet</h2>
            <p className="mb-6 max-w-md text-sm text-muted-foreground">
              Agents are discovered from your conversation history. Create one to get started, or
              start a conversation in an agent workspace.
            </p>
            <Button onClick={() => setShowCreateDialog(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Create Agent
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <button
                key={agent.path}
                className="group flex flex-col rounded-lg border bg-card p-4 text-left transition-colors hover:bg-accent/50"
                onClick={() => handleAgentClick(agent.name)}
              >
                <div className="mb-3 flex items-center gap-3">
                  {agent.hasAvatar && agent.path ? (
                    <img
                      src={`${baseUrl}/api/v2/agents/avatar?path=${encodeURIComponent(agent.path)}`}
                      alt={agent.name}
                      className="h-10 w-10 rounded-full object-cover"
                    />
                  ) : (
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-blue-500/10">
                      <Bot className="h-5 w-5 text-blue-500" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate font-medium">{agent.name}</h3>
                    {agent.description && (
                      <p className="truncate text-xs text-muted-foreground">{agent.description}</p>
                    )}
                  </div>
                </div>

                <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <MessageSquare className="h-3 w-3" />
                    {agent.conversationCount} conversation
                    {agent.conversationCount !== 1 ? 's' : ''}
                  </span>
                  <span>{formatDistanceToNow(new Date(agent.lastUsed), { addSuffix: true })}</span>
                </div>

                {agent.urls?.dashboard && /^https?:\/\//i.test(agent.urls.dashboard) && (
                  <a
                    href={agent.urls.dashboard}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <ExternalLink className="h-3 w-3" />
                    Dashboard
                  </a>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      <CreateAgentDialog
        open={showCreateDialog}
        onOpenChange={setShowCreateDialog}
        onAgentCreated={handleAgentCreated}
      />
    </div>
  );
};
