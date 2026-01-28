import type { FC } from 'react';
import { AlertCircle } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import type { Task } from '@/types/task';

interface SuggestedActionsTipsProps {
  task: Task;
}

const SuggestedActionsTips: FC<SuggestedActionsTipsProps> = ({ task }) => {
  const getTipMessage = () => {
    switch (task.status) {
      case 'pending':
        if (task.git?.pr_url) {
          return 'Task work is complete. The PR is open and waiting for review or merge.';
        }
        if (task.conversation_ids?.length) {
          return 'Task is in progress. Check the conversation for current status.';
        }
        return "Task is ready to start. Begin working on it when you're ready.";

      case 'active':
        return 'You can view the conversation to see real-time progress and interact with the task.';

      case 'completed':
        return 'Great job! Consider reviewing the results and creating follow-up tasks if needed.';

      case 'failed':
        return 'Check the error details and workspace to understand what went wrong before retrying.';

      default:
        return 'Task status updated. Check the details for more information.';
    }
  };

  return (
    <Card className="border-blue-200 bg-blue-50/50">
      <CardContent className="pt-4">
        <div className="flex items-start gap-2">
          <AlertCircle className="mt-0.5 h-4 w-4 text-blue-600" />
          <div className="text-sm">
            <p className="font-medium text-blue-900">Tip:</p>
            <p className="mt-1 text-blue-700">{getTipMessage()}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export { SuggestedActionsTips };
