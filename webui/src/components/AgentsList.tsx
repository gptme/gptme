import { Bot, Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { extractAgentsFromConversations, type Agent } from '@/utils/workspaceUtils';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatDistanceToNow } from 'date-fns';
import type { ConversationSummary } from '@/types/conversation';
import { selectedAgent$ } from '@/stores/sidebar';
import { use$ } from '@legendapp/state/react';
import type { FC } from 'react';

interface AgentsListProps {
  conversations: ConversationSummary[];
  onSelectAgent?: (agent: Agent) => void;
  handleCreateAgent?: () => void;
}

export const AgentsList: FC<AgentsListProps> = ({
  conversations,
  onSelectAgent,
  handleCreateAgent,
}) => {
  const agents = extractAgentsFromConversations(conversations);
  const selectedAgent = use$(selectedAgent$);

  const handleSelectAgent = (agent: Agent) => {
    // Toggle selection - if already selected, deselect
    if (selectedAgent && selectedAgent.path === agent.path) {
      selectedAgent$.set(null);
    } else {
      selectedAgent$.set(agent);
    }
    onSelectAgent?.(agent);
  };

  const handleClearSelection = () => {
    selectedAgent$.set(null);
  };

  if (agents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-4 text-center text-sm text-muted-foreground">
        <Bot className="mb-2 h-8 w-8 opacity-50" />
        <p>No agents yet</p>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" onClick={handleCreateAgent} className="mt-2">
              <Plus className="mr-1 h-4 w-4" />
              Create Agent
            </Button>
          </TooltipTrigger>
          <TooltipContent>Create a specialized agent with custom context</TooltipContent>
        </Tooltip>
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col space-y-1 p-2">
        {agents.map((agent) => {
          const isSelected = selectedAgent?.path === agent.path;
          return (
            <Button
              key={agent.path}
              variant={isSelected ? 'default' : 'ghost'}
              className={`h-auto flex-col items-start justify-start p-2 text-left ${
                isSelected ? 'bg-primary text-primary-foreground' : ''
              }`}
              onClick={() => handleSelectAgent(agent)}
            >
              <div className="flex w-full items-center justify-between">
                <div className="flex items-center space-x-2">
                  <Bot
                    className={`h-4 w-4 ${isSelected ? 'text-primary-foreground' : 'text-blue-500'}`}
                  />
                  <span className="text-sm font-medium">{agent.name}</span>
                </div>
                <span
                  className={`text-xs ${isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}
                >
                  {agent.conversationCount}
                </span>
              </div>
              <div className="mt-1 flex w-full items-center justify-between">
                {agent.description && (
                  <p
                    className={`truncate text-xs ${isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}
                  >
                    {agent.description}
                  </p>
                )}
                <span
                  className={`text-xs ${isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}
                >
                  {formatDistanceToNow(new Date(agent.lastUsed), { addSuffix: true })}
                </span>
              </div>
            </Button>
          );
        })}
      </div>

      {/* Clear filter button when an agent is selected */}
      {selectedAgent && (
        <div className="px-2 pb-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-full text-xs"
            onClick={handleClearSelection}
          >
            <X className="mr-1 h-3 w-3" />
            Show all conversations
          </Button>
        </div>
      )}
    </>
  );
};
