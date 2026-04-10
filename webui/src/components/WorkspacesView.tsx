import { Folder, MessageSquare } from 'lucide-react';
import { extractWorkspacesFromConversations, formatPath } from '@/utils/workspaceUtils';
import { formatDistanceToNow } from 'date-fns';
import type { ConversationSummary } from '@/types/conversation';
import { useNavigate } from 'react-router-dom';
import { selectedWorkspace$ } from '@/stores/sidebar';
import type { FC } from 'react';

interface WorkspacesViewProps {
  conversations: ConversationSummary[];
}

export const WorkspacesView: FC<WorkspacesViewProps> = ({ conversations }) => {
  const navigate = useNavigate();
  const workspaces = extractWorkspacesFromConversations(conversations);

  const handleWorkspaceClick = (path: string) => {
    selectedWorkspace$.set(path);
    navigate('/chat');
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold">Workspaces</h1>
          <p className="text-sm text-muted-foreground">
            {workspaces.length} workspace{workspaces.length !== 1 ? 's' : ''} from your
            conversations
          </p>
        </div>

        {workspaces.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Folder className="mb-4 h-16 w-16 text-muted-foreground opacity-40" />
            <h2 className="mb-2 text-lg font-medium">No workspaces yet</h2>
            <p className="max-w-md text-sm text-muted-foreground">
              Workspaces appear automatically when you create conversations in different
              directories.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {workspaces.map((workspace) => (
              <button
                key={workspace.path}
                className="group flex flex-col rounded-lg border bg-card p-4 text-left transition-colors hover:bg-accent/50"
                onClick={() => handleWorkspaceClick(workspace.path)}
              >
                <div className="mb-3 flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-yellow-500/10">
                    <Folder className="h-5 w-5 text-yellow-600" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate font-medium">{workspace.name}</h3>
                    <p className="truncate text-xs text-muted-foreground">
                      {formatPath(workspace.path)}
                    </p>
                  </div>
                </div>

                <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <MessageSquare className="h-3 w-3" />
                    {workspace.conversationCount} conversation
                    {workspace.conversationCount !== 1 ? 's' : ''}
                  </span>
                  <span>
                    {formatDistanceToNow(new Date(workspace.lastUsed), { addSuffix: true })}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
