import type { Task, CreateTaskRequest, TaskAction } from '@/types/task';
import { getApiBaseUrl, getAuthHeader } from '@/utils/connectionConfig';

/**
 * Helper function to add auth headers to fetch options
 */
const getFetchOptions = (options: RequestInit = {}): RequestInit => {
  const authHeader = getAuthHeader();
  const headers = {
    ...options.headers,
  };

  if (authHeader) {
    (headers as Record<string, string>)['Authorization'] = authHeader;
  }

  return {
    ...options,
    headers,
  };
};

/**
 * Task API client for interacting with the gptme tasks backend.
 */
export const taskApi = {
  /**
   * List all tasks with current status information.
   */
  async listTasks(includeArchived: boolean = false): Promise<Task[]> {
    const url = new URL(`${getApiBaseUrl()}/api/v2/tasks`);
    if (includeArchived) {
      url.searchParams.set('include_archived', 'true');
    }

    const response = await fetch(url.toString(), getFetchOptions());
    if (!response.ok) {
      throw new Error(`Failed to list tasks: ${response.statusText}`);
    }
    return response.json();
  },

  /**
   * Create a new task and initial conversation.
   */
  async createTask(request: CreateTaskRequest): Promise<Task> {
    const response = await fetch(
      `${getApiBaseUrl()}/api/v2/tasks`,
      getFetchOptions({
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
      })
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || `Failed to create task: ${response.statusText}`);
    }

    return response.json();
  },

  /**
   * Get detailed information about a specific task.
   */
  async getTask(taskId: string): Promise<Task> {
    const response = await fetch(`${getApiBaseUrl()}/api/v2/tasks/${taskId}`, getFetchOptions());
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error(`Task not found: ${taskId}`);
      }
      throw new Error(`Failed to get task: ${response.statusText}`);
    }
    return response.json();
  },

  /**
   * Update task metadata.
   */
  async updateTask(taskId: string, updates: Partial<CreateTaskRequest>): Promise<Task> {
    const response = await fetch(
      `${getApiBaseUrl()}/api/v2/tasks/${taskId}`,
      getFetchOptions({
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(updates),
      })
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || `Failed to update task: ${response.statusText}`);
    }

    return response.json();
  },

  /**
   * Archive a task (hide from active view but preserve data).
   */
  async archiveTask(taskId: string): Promise<void> {
    const response = await fetch(
      `${getApiBaseUrl()}/api/v2/tasks/${taskId}/archive`,
      getFetchOptions({
        method: 'POST',
      })
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || `Failed to archive task: ${response.statusText}`);
    }
  },

  /**
   * Unarchive a task (restore to active view).
   */
  async unarchiveTask(taskId: string): Promise<void> {
    const response = await fetch(
      `${getApiBaseUrl()}/api/v2/tasks/${taskId}/unarchive`,
      getFetchOptions({
        method: 'POST',
      })
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || `Failed to unarchive task: ${response.statusText}`);
    }
  },

  /**
   * Create a new conversation to continue work on a task.
   */
  async continueTask(taskId: string): Promise<{ conversation_id: string }> {
    const response = await fetch(
      `${getApiBaseUrl()}/api/v2/tasks/${taskId}/continue`,
      getFetchOptions({
        method: 'POST',
      })
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(error.error || `Failed to continue task: ${response.statusText}`);
    }

    return response.json();
  },

  /**
   * Get suggested actions for a task based on its current status.
   */
  async getSuggestedActions(taskId: string): Promise<TaskAction[]> {
    const response = await fetch(
      `${getApiBaseUrl()}/api/v2/tasks/${taskId}/actions`,
      getFetchOptions()
    );
    if (!response.ok) {
      throw new Error(`Failed to get task actions: ${response.statusText}`);
    }

    const result = await response.json();
    return result.actions || [];
  },

  /**
   * Navigate to the conversation interface for a task.
   */
  viewTaskConversation(taskId: string): void {
    // Navigate to the conversation page for this task
    window.location.href = `/chat/${taskId}`;
  },

  /**
   * Execute a task action (delegated to specific action handlers).
   */
  async executeAction(
    taskId: string,
    actionId: string,
    params?: Record<string, unknown>
  ): Promise<unknown> {
    switch (actionId) {
      case 'start':
        return this.startTask(taskId);

      case 'view_conversation':
        this.viewTaskConversation(taskId);
        return { status: 'redirect' };

      case 'continue':
        return this.continueTask(taskId);

      case 'archive':
        await this.archiveTask(taskId);
        return { status: 'archived' };

      case 'unarchive':
        await this.unarchiveTask(taskId);
        return { status: 'unarchived' };

      case 'edit':
        // Return indication that edit dialog should be opened
        return { status: 'edit_requested', taskId };

      case 'view_pr':
        if (params?.target_repo) {
          window.open(`https://github.com/${params.target_repo}/pulls`, '_blank');
        }
        return { status: 'opened_pr' };

      default:
        throw new Error(`Unknown action: ${actionId}`);
    }
  },

  /**
   * Start a task by creating a conversation session and beginning generation.
   * This delegates to the existing conversation API.
   */
  async startTask(taskId: string): Promise<{
    status: 'started';
    conversation_id: string;
    session_id: string;
  }> {
    try {
      // First, get the task to find its conversation ID
      const task = await this.getTask(taskId);

      if (!task.conversation_ids || task.conversation_ids.length === 0) {
        throw new Error('Task has no associated conversations');
      }

      // Get the latest conversation ID
      const conversationId = task.conversation_ids[task.conversation_ids.length - 1];

      // Create a session for this conversation (using existing v2 API)
      const sessionResponse = await fetch(
        `${getApiBaseUrl()}/api/v2/conversations/${conversationId}`,
        getFetchOptions({
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            messages: [],
            config: {
              chat: {
                stream: true,
                interactive: true,
              },
            },
          }),
        })
      );

      if (!sessionResponse.ok) {
        throw new Error('Failed to create conversation session');
      }

      const sessionData = await sessionResponse.json();

      // Start a step (generation) in the conversation
      const stepResponse = await fetch(
        `${getApiBaseUrl()}/api/v2/conversations/${conversationId}/step`,
        getFetchOptions({
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            session_id: sessionData.session_id,
            auto_confirm: false,
            stream: true,
          }),
        })
      );

      if (!stepResponse.ok) {
        throw new Error('Failed to start task generation');
      }

      return {
        status: 'started',
        conversation_id: conversationId,
        session_id: sessionData.session_id,
      };
    } catch (error) {
      console.error('Error starting task:', error);
      throw error;
    }
  },

  /**
   * Check if the gptme server is available.
   */
  async checkServerHealth(): Promise<boolean> {
    try {
      const response = await fetch(
        `${getApiBaseUrl()}/api/v2`,
        getFetchOptions({
          method: 'GET',
          // Add timeout
          signal: AbortSignal.timeout(5000),
        })
      );
      return response.ok;
    } catch {
      return false;
    }
  },
};

/**
 * Task API error handling utility.
 */
export class TaskApiError extends Error {
  constructor(
    message: string,
    public statusCode?: number
  ) {
    super(message);
    this.name = 'TaskApiError';
  }
}

/**
 * Wrapper for API calls with error handling.
 */
export async function withErrorHandling<T>(
  apiCall: () => Promise<T>,
  errorMessage: string = 'API call failed'
): Promise<T> {
  try {
    return await apiCall();
  } catch (error) {
    console.error(`${errorMessage}:`, error);
    if (error instanceof Error) {
      throw new TaskApiError(`${errorMessage}: ${error.message}`);
    }
    throw new TaskApiError(errorMessage);
  }
}
