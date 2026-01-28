export type TaskStatus = 'pending' | 'active' | 'completed' | 'failed';
export type TargetType = 'stdout' | 'pr' | 'email' | 'tweet';

export interface Task {
  id: string;
  content: string;
  created_at: string;
  status: TaskStatus;
  target_type: TargetType;
  target_repo?: string;
  conversation_ids: string[];
  metadata: Record<string, unknown>;
  archived: boolean;
  error?: string;

  // Derived fields from backend
  workspace?: string;
  conversation?: {
    id: string;
    name: string;
    message_count: number;
  };
  git?: {
    branch: string;
    clean: boolean;
    files: string[];
    remote_url?: string;
    pr_url?: string;
    pr_status?: 'OPEN' | 'MERGED' | 'CLOSED';
    pr_merged?: boolean;
    diff_stats?: {
      files_changed: number;
      lines_added: number;
      lines_removed: number;
    };
    recent_commits?: string[];
    error?: string;
  };
  progress?: {
    current_step?: string;
    steps_completed?: number;
    total_steps?: number;
  };
}

export interface CreateTaskRequest {
  content: string;
  target_type?: TargetType;
  target_repo?: string;
  workspace?: string;
}

export interface TaskAction {
  id: string;
  label: string;
  description: string;
  type: 'primary' | 'secondary' | 'destructive';
  icon?: string;
}

export interface SuggestedActions {
  task_id: string;
  actions: TaskAction[];
}
