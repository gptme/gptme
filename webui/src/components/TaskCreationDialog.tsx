import { type FC, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { GitBranch, Folder, Settings } from 'lucide-react';
import { WorkspaceSelector } from '@/components/WorkspaceSelector';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import type { CreateTaskRequest, TargetType } from '@/types/task';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTaskCreated: (task: CreateTaskRequest) => void;
}

const TaskCreationDialog: FC<Props> = ({ open, onOpenChange, onTaskCreated }) => {
  const [formData, setFormData] = useState<CreateTaskRequest>({
    content: '',
    target_type: 'pr',
    target_repo: '',
    workspace: '',
  });
  const [useWorktree, setUseWorktree] = useState(true);
  const [createBranch, setCreateBranch] = useState(true);
  const [useExistingWorkspace, setUseExistingWorkspace] = useState(true);
  const [isLoading, setIsLoading] = useState(false);

  // Get available workspaces
  const { workspaces, addCustomWorkspace } = useWorkspaces();

  const handleSubmit = async () => {
    if (!formData.content.trim()) return;

    setIsLoading(true);
    try {
      // Simulate API call
      await new Promise((resolve) => setTimeout(resolve, 1000));

      const taskData: CreateTaskRequest = {
        ...formData,
        content: formData.content.trim(),
      };

      onTaskCreated(taskData);

      // Reset form
      setFormData({
        content: '',
        target_type: 'pr',
        target_repo: '',
        workspace: '',
      });
      setUseWorktree(true);
      setCreateBranch(true);
      setUseExistingWorkspace(true);
    } catch (error) {
      console.error('Error creating task:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleInputChange = (field: keyof CreateTaskRequest, value: string) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create New Task</DialogTitle>
          <DialogDescription>
            Set up a new development task with isolated workspace and git integration.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6">
          {/* Task Description */}
          <div className="space-y-2">
            <Label htmlFor="content">Task Description *</Label>
            <Textarea
              id="content"
              placeholder="Describe what you want to accomplish..."
              value={formData.content}
              onChange={(e) => handleInputChange('content', e.target.value)}
              rows={4}
              className="resize-none"
            />
          </div>

          {/* Target Type */}
          <div className="space-y-2">
            <Label htmlFor="target-type">Target Type</Label>
            <Select
              value={formData.target_type}
              onValueChange={(value) => handleInputChange('target_type', value as TargetType)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select target type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pr">Pull Request</SelectItem>
                <SelectItem value="stdout">Console Output</SelectItem>
                <SelectItem value="email">Email</SelectItem>
                <SelectItem value="tweet">Tweet</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Target Repository (for PR tasks) */}
          {formData.target_type === 'pr' && (
            <div className="space-y-2">
              <Label htmlFor="target-repo">Target Repository</Label>
              <Input
                id="target-repo"
                placeholder="e.g., username/repository-name"
                value={formData.target_repo}
                onChange={(e) => handleInputChange('target_repo', e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                GitHub repository where the PR will be created
              </p>
            </div>
          )}

          {/* Workspace Settings */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-4 w-4" />
                Workspace Settings
              </CardTitle>
              <CardDescription>Configure isolated development environment</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Workspace Selection Mode Toggle */}
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <Folder className="h-4 w-4" />
                    <Label htmlFor="use-existing-workspace">Use Existing Workspace</Label>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Choose from recent workspaces or enter custom path
                  </p>
                </div>
                <Switch
                  id="use-existing-workspace"
                  checked={useExistingWorkspace}
                  onCheckedChange={setUseExistingWorkspace}
                />
              </div>

              {/* Workspace Selection */}
              {useExistingWorkspace ? (
                <WorkspaceSelector
                  label="Select Workspace"
                  selectedWorkspace={formData.workspace || '.'}
                  onWorkspaceChange={(workspace) => handleInputChange('workspace', workspace)}
                  workspaces={workspaces}
                  placeholder="Choose a workspace"
                  showConversationCount={true}
                  allowCustomPath={true}
                  onAddWorkspace={(path: string) => {
                    console.log('[TaskCreationDialog] Adding new workspace:', path);
                    addCustomWorkspace(path);
                  }}
                />
              ) : (
                <div className="space-y-2">
                  <Label htmlFor="workspace">Custom Workspace Path</Label>
                  <Input
                    id="workspace"
                    placeholder="Enter custom workspace path or leave empty for auto-generated"
                    value={formData.workspace}
                    onChange={(e) => handleInputChange('workspace', e.target.value)}
                  />
                </div>
              )}

              {/* Git Worktree Option */}
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <GitBranch className="h-4 w-4" />
                    <Label htmlFor="use-worktree">Use Git Worktree</Label>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Create isolated git worktree for this task
                  </p>
                </div>
                <Switch id="use-worktree" checked={useWorktree} onCheckedChange={setUseWorktree} />
              </div>

              {/* Branch Creation Option */}
              {useWorktree && (
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <Folder className="h-4 w-4" />
                      <Label htmlFor="create-branch">Create New Branch</Label>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Create a new branch for this task (task/&lt;task-id&gt;)
                    </p>
                  </div>
                  <Switch
                    id="create-branch"
                    checked={createBranch}
                    onCheckedChange={setCreateBranch}
                  />
                </div>
              )}
            </CardContent>
          </Card>

          {/* Task Preview */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Task Preview</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Type:</span>
                  <span className="capitalize">{formData.target_type}</span>
                </div>
                {formData.target_repo && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Repository:</span>
                    <span>{formData.target_repo}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Git Worktree:</span>
                  <span>{useWorktree ? 'Yes' : 'No'}</span>
                </div>
                {useWorktree && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">New Branch:</span>
                    <span>{createBranch ? 'Yes' : 'No'}</span>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!formData.content.trim() || isLoading}>
            {isLoading ? 'Creating...' : 'Create Task'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export { TaskCreationDialog };
