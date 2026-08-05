import { Folder, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  extractWorkspacesFromConversations,
  formatPath,
  type WorkspaceProject,
} from '@/utils/workspaceUtils';
import { formatDistanceToNow } from 'date-fns';
import type { ConversationSummary } from '@/types/conversation';
import { selectedWorkspace$ } from '@/stores/sidebar';
import { use$ } from '@legendapp/state/react';
import type { FC } from 'react';

interface WorkspaceListProps {
  conversations: ConversationSummary[];
}

export const WorkspaceList: FC<WorkspaceListProps> = ({ conversations }) => {
  const workspaces = extractWorkspacesFromConversations(conversations);
  const selectedWorkspace = use$(selectedWorkspace$);

  const handleSelectWorkspace = (workspace: WorkspaceProject) => {
    // Toggle selection - if already selected, deselect
    if (selectedWorkspace === workspace.path) {
      selectedWorkspace$.set(null);
    } else {
      selectedWorkspace$.set(workspace.path);
    }
  };

  const handleClearSelection = () => {
    selectedWorkspace$.set(null);
  };

  if (workspaces.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-4 text-center text-sm text-muted-foreground">
        <Folder className="mb-2 h-8 w-8 opacity-50" />
        <p>No workspaces yet</p>
        <p className="text-xs">Workspaces appear when you create conversations</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col space-y-1 p-2">
      {workspaces.map((workspace) => {
        const isSelected = selectedWorkspace === workspace.path;
        return (
          <Button
            key={workspace.path}
            variant={isSelected ? 'default' : 'ghost'}
            className={`h-auto flex-col items-start justify-start p-2 text-left ${
              isSelected ? 'bg-primary text-primary-foreground' : ''
            }`}
            onClick={() => handleSelectWorkspace(workspace)}
          >
            <div className="flex w-full items-center justify-between">
              <div className="flex items-center space-x-2">
                <Folder
                  className={`h-4 w-4 ${isSelected ? 'text-primary-foreground' : 'text-yellow-600'}`}
                />
                <span className="text-sm font-medium">{workspace.name}</span>
              </div>
              <span
                className={`text-xs ${isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}
              >
                {workspace.conversationCount}
              </span>
            </div>
            <div className="mt-1 flex w-full items-center justify-between">
              <p
                className={`truncate text-xs ${isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}
              >
                {formatPath(workspace.path)}
              </p>
              <span
                className={`text-xs ${isSelected ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}
              >
                {formatDistanceToNow(new Date(workspace.lastUsed), { addSuffix: true })}
              </span>
            </div>
          </Button>
        );
      })}

      {/* Clear filter button when a workspace is selected */}
      {selectedWorkspace && (
        <Button
          variant="outline"
          size="sm"
          className="mt-2 h-8 text-xs"
          onClick={handleClearSelection}
        >
          <X className="mr-1 h-3 w-3" />
          Show all conversations
        </Button>
      )}
    </div>
  );
};
