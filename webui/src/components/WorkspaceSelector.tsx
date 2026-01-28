import { type FC, useState } from 'react';
import { Folder, Plus, Check } from 'lucide-react';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { formatPath } from '@/utils/workspaceUtils';
import type { WorkspaceProject } from '@/utils/workspaceUtils';

interface Props {
  label?: string;
  selectedWorkspace: string;
  onWorkspaceChange: (workspace: string) => void;
  workspaces: WorkspaceProject[];
  disabled?: boolean;
  placeholder?: string;
  showCurrentDirectory?: boolean;
  showConversationCount?: boolean;
  className?: string;
  onAddWorkspace?: (path: string) => void;
  allowCustomPath?: boolean;
}

export const WorkspaceSelector: FC<Props> = ({
  label = 'Workspace',
  selectedWorkspace,
  onWorkspaceChange,
  workspaces,
  disabled = false,
  placeholder = 'Current directory (.)',
  showCurrentDirectory = true,
  showConversationCount = false,
  className,
  onAddWorkspace,
  allowCustomPath = false,
}) => {
  const [newWorkspacePath, setNewWorkspacePath] = useState('');
  const [isAddingWorkspace, setIsAddingWorkspace] = useState(false);

  const handleAddWorkspace = () => {
    if (newWorkspacePath.trim() && onAddWorkspace) {
      onAddWorkspace(newWorkspacePath.trim());
      onWorkspaceChange(newWorkspacePath.trim());
      setNewWorkspacePath('');
      setIsAddingWorkspace(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAddWorkspace();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setIsAddingWorkspace(false);
      setNewWorkspacePath('');
    }
  };

  return (
    <div className={`space-y-1 ${className || ''}`}>
      {label && <Label htmlFor="workspace-select">{label}</Label>}
      <Select value={selectedWorkspace} onValueChange={onWorkspaceChange} disabled={disabled}>
        <SelectTrigger id="workspace-select">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {showCurrentDirectory && <SelectItem value=".">Current directory (.)</SelectItem>}
          {workspaces.map((workspace) => (
            <SelectItem key={workspace.path} value={workspace.path}>
              <div className="flex items-center space-x-2">
                <Folder className="h-4 w-4 text-yellow-600" />
                <div className="flex flex-col">
                  <div className="flex items-center gap-2">
                    <span className="text-left text-sm font-medium">{workspace.name}</span>
                    {showConversationCount && (
                      <span className="text-xs text-muted-foreground">
                        ({workspace.conversationCount})
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {formatPath(workspace.path)}
                  </span>
                </div>
              </div>
            </SelectItem>
          ))}

          {allowCustomPath && onAddWorkspace && (
            <>
              <Separator className="my-1" />
              <div className="p-2">
                {!isAddingWorkspace ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start text-sm"
                    onClick={() => setIsAddingWorkspace(true)}
                    disabled={disabled}
                  >
                    <Plus className="mr-2 h-4 w-4" />
                    Add new workspace path
                  </Button>
                ) : (
                  <div className="space-y-2">
                    <Input
                      placeholder="Enter workspace path (e.g., /path/to/project)"
                      value={newWorkspacePath}
                      onChange={(e) => setNewWorkspacePath(e.target.value)}
                      onKeyDown={handleKeyDown}
                      className="text-sm"
                      autoFocus
                    />
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        onClick={handleAddWorkspace}
                        disabled={!newWorkspacePath.trim()}
                        className="flex-1"
                      >
                        <Check className="mr-1 h-3 w-3" />
                        Add
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setIsAddingWorkspace(false);
                          setNewWorkspacePath('');
                        }}
                        className="flex-1"
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </SelectContent>
      </Select>
    </div>
  );
};
