import { type FC } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  GitBranch,
  Folder,
  Calendar,
  Clock,
  ExternalLink,
  Terminal,
  AlertCircle,
  CheckCircle,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { SuggestedActionsPanel } from '@/components/SuggestedActionsPanel';
import { SuggestedActionsTips } from '@/components/SuggestedActionsTips';
import type { Task, TaskStatus } from '@/types/task';

interface Props {
  task: Task;
}

const TaskDetails: FC<Props> = ({ task }) => {
  const navigate = useNavigate();

  const getStatusIcon = (status: TaskStatus) => {
    switch (status) {
      case 'pending':
        return <Clock className="h-5 w-5 text-yellow-500" />;
      case 'active':
        return <RefreshCw className="h-5 w-5 animate-spin text-blue-500" />;
      case 'completed':
        return <CheckCircle className="h-5 w-5 text-green-500" />;
      case 'failed':
        return <XCircle className="h-5 w-5 text-red-500" />;
    }
  };

  const getStatusColor = (status: TaskStatus) => {
    switch (status) {
      case 'pending':
        return 'text-yellow-600';
      case 'active':
        return 'text-blue-600';
      case 'completed':
        return 'text-green-600';
      case 'failed':
        return 'text-red-600';
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return {
      date: date.toLocaleDateString(),
      time: date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      relative: getRelativeTime(date),
    };
  };

  const getRelativeTime = (date: Date) => {
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / (1000 * 60));
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  };

  const openWorkspace = () => {
    if (task.workspace && task.conversation_ids.length > 0) {
      // Navigate to workspace explorer using the latest conversation ID
      const conversationId = task.conversation_ids[task.conversation_ids.length - 1];
      navigate(`/workspace/${conversationId}`);
    }
  };

  const openRepository = () => {
    if (task.target_repo) {
      window.open(`https://github.com/${task.target_repo}`, '_blank');
    }
  };

  const createdAt = formatDate(task.created_at);
  const progressPercentage = task.progress
    ? ((task.progress.steps_completed || 0) / (task.progress.total_steps || 1)) * 100
    : 0;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl space-y-6">
          {/* Header */}
          <div className="space-y-4">
            <div className="flex items-start justify-between">
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  {getStatusIcon(task.status)}
                  <h1 className="text-2xl font-bold">Task Details</h1>
                </div>
                <Badge variant="outline" className={`capitalize ${getStatusColor(task.status)}`}>
                  {task.status}
                </Badge>
              </div>

              {task.target_type === 'pr' && (
                <div className="flex gap-2">
                  {task.workspace && (
                    <Button variant="outline" size="sm" onClick={openWorkspace}>
                      <Terminal className="mr-1 h-4 w-4" />
                      Open Workspace
                    </Button>
                  )}
                  {task.target_repo && (
                    <Button variant="outline" size="sm" onClick={openRepository}>
                      <ExternalLink className="mr-1 h-4 w-4" />
                      Repository
                    </Button>
                  )}
                </div>
              )}
            </div>

            <Separator />
          </div>

          {/* Task Description */}
          <Card>
            <CardHeader>
              <CardTitle>Description</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{task.content}</p>
            </CardContent>
          </Card>

          {/* Progress (for active tasks) */}
          {task.status === 'active' && task.progress && (
            <Card>
              <CardHeader>
                <CardTitle>Progress</CardTitle>
                <CardDescription>
                  {task.progress.steps_completed} of {task.progress.total_steps} steps completed
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Progress value={progressPercentage} className="w-full" />

                {task.progress.current_step && (
                  <div className="flex items-center gap-2 text-sm">
                    <RefreshCw className="h-4 w-4 animate-spin text-blue-500" />
                    <span className="text-muted-foreground">Current step:</span>
                    <span className="font-medium">{task.progress.current_step}</span>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Error Information (for failed tasks) */}
          {task.status === 'failed' && task.error && (
            <Card className="border-red-200 bg-red-50/50">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-red-700">
                  <AlertCircle className="h-5 w-5" />
                  Error Details
                </CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-red-100 p-3 text-sm">
                  {task.error}
                </pre>
              </CardContent>
            </Card>
          )}

          {/* Task Information */}
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Task Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center gap-2 text-sm">
                  <Calendar className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Created:</span>
                  <span className="font-medium">
                    {createdAt.date} at {createdAt.time}
                  </span>
                  <span className="text-muted-foreground">({createdAt.relative})</span>
                </div>

                <div className="flex items-center gap-2 text-sm">
                  <ExternalLink className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Target:</span>
                  <Badge variant="secondary" className="capitalize">
                    {task.target_type}
                  </Badge>
                </div>

                {task.target_repo && (
                  <div className="flex items-center gap-2 text-sm">
                    <GitBranch className="h-4 w-4 text-muted-foreground" />
                    <span className="text-muted-foreground">Repository:</span>
                    <button
                      onClick={openRepository}
                      className="font-medium text-blue-600 hover:underline"
                    >
                      {task.target_repo}
                    </button>
                  </div>
                )}

                <div className="flex items-center gap-2 text-sm">
                  <Terminal className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Task ID:</span>
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">{task.id}</code>
                </div>

                {/* Conversations */}
                {task.conversation_ids && task.conversation_ids.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-sm">
                      <Terminal className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">Conversations:</span>
                    </div>
                    <div className="ml-6 space-y-1">
                      {task.conversation_ids.map((conversationId, index) => {
                        const isActive = index === task.conversation_ids.length - 1;
                        return (
                          <div key={conversationId} className="flex items-center gap-2">
                            <button
                              onClick={() => navigate(`/chat/${conversationId}`)}
                              className={`font-mono text-xs hover:underline ${
                                isActive
                                  ? 'font-semibold text-blue-600'
                                  : 'text-muted-foreground hover:text-foreground'
                              }`}
                            >
                              {conversationId}
                            </button>
                            {isActive && (
                              <Badge variant="secondary" className="text-xs">
                                Current
                              </Badge>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Git Information */}
            {(task.workspace || task.git?.branch) && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Git & Development Info</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {task.workspace && (
                    <div>
                      <div className="flex items-center gap-2 text-sm">
                        <Folder className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">Workspace:</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="mr-2 h-4 w-4" />
                        <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                          {task.workspace}
                        </code>
                      </div>
                    </div>
                  )}

                  {task.git?.branch && (
                    <div className="flex items-center gap-2 text-sm">
                      <GitBranch className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">Branch:</span>
                      <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                        {task.git.branch}
                      </code>
                    </div>
                  )}

                  {/* PR Link */}
                  {task.git?.pr_url && (
                    <div className="flex items-center gap-2 text-sm">
                      <GitBranch className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">Pull Request:</span>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-6 px-2"
                          onClick={() => window.open(task.git!.pr_url, '_blank')}
                        >
                          <ExternalLink className="mr-1 h-3 w-3" />
                          View PR
                        </Button>
                        {task.git.pr_status && (
                          <Badge
                            variant={
                              task.git.pr_status === 'MERGED'
                                ? 'default'
                                : task.git.pr_status === 'OPEN'
                                  ? 'secondary'
                                  : 'destructive'
                            }
                            className={`text-xs ${
                              task.git.pr_status === 'MERGED'
                                ? 'bg-green-100 text-green-800'
                                : task.git.pr_status === 'OPEN'
                                  ? 'bg-blue-100 text-blue-800'
                                  : 'bg-red-100 text-red-800'
                            }`}
                          >
                            {task.git.pr_status}
                          </Badge>
                        )}
                      </div>
                    </div>
                  )}

                  {/* File Changes Statistics */}
                  {task.git?.diff_stats &&
                    (task.git.diff_stats.files_changed > 0 ||
                      task.git.diff_stats.lines_added > 0 ||
                      task.git.diff_stats.lines_removed > 0) && (
                      <div className="rounded bg-muted/50 p-3">
                        <h4 className="mb-2 text-sm font-medium">Changes Summary</h4>
                        <div className="grid grid-cols-3 gap-4 text-xs">
                          <div className="text-center">
                            <div className="font-bold text-blue-600">
                              {task.git.diff_stats.files_changed}
                            </div>
                            <div className="text-muted-foreground">Files</div>
                          </div>
                          <div className="text-center">
                            <div className="font-bold text-green-600">
                              +{task.git.diff_stats.lines_added}
                            </div>
                            <div className="text-muted-foreground">Added</div>
                          </div>
                          <div className="text-center">
                            <div className="font-bold text-red-600">
                              -{task.git.diff_stats.lines_removed}
                            </div>
                            <div className="text-muted-foreground">Removed</div>
                          </div>
                        </div>
                      </div>
                    )}

                  {/* Recent Commits */}
                  {task.git?.recent_commits && task.git.recent_commits.length > 0 && (
                    <div>
                      <h4 className="mb-2 text-sm font-medium">Recent Commits</h4>
                      <div className="space-y-1">
                        {task.git.recent_commits.slice(0, 3).map((commit, index) => (
                          <div key={index} className="text-xs">
                            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                              {commit}
                            </code>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Git Status */}
                  {task.git && (
                    <div className="flex items-center gap-2 text-sm">
                      <CheckCircle
                        className={`h-4 w-4 ${task.git.clean ? 'text-green-500' : 'text-yellow-500'}`}
                      />
                      <span className="text-muted-foreground">Status:</span>
                      <span className={task.git.clean ? 'text-green-600' : 'text-yellow-600'}>
                        {task.git.clean ? 'Clean' : `${task.git.files?.length || 0} modified files`}
                      </span>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
          <SuggestedActionsPanel task={task} />
          <SuggestedActionsTips task={task} />
        </div>
      </div>
    </div>
  );
};

export { TaskDetails };
