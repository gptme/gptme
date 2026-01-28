import React from 'react';
import { observable, mergeIntoObservable } from '@legendapp/state';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { taskApi } from '@/utils/taskApi';
import type { Task, CreateTaskRequest } from '@/types/task';

export interface TaskState {
  // The task data
  data: Task;
  // Whether this task is currently being updated
  isUpdating: boolean;
}

// Central store for all tasks
export const tasks$ = observable(new Map<string, TaskState>());

// Currently selected task
export const selectedTask$ = observable<string | null>(null);

// Whether to show archived tasks
export const showArchived$ = observable(false);

// Helper functions
export function updateTask(id: string, update: Partial<Task>) {
  const existing = tasks$.get(id);
  if (existing) {
    mergeIntoObservable(existing.data, update);
  }
}

export function setTaskUpdating(id: string, isUpdating: boolean) {
  const existing = tasks$.get(id);
  if (existing) {
    existing.isUpdating.set(isUpdating);
  }
}

// Initialize a task in the store
export function initTask(task: Task) {
  const initial: TaskState = {
    data: task,
    isUpdating: false,
  };
  tasks$.set(task.id, initial);
}

// Update multiple tasks
export function updateTasks(newTasks: Task[]) {
  // Clear existing tasks that aren't in the new list
  const newTaskIds = new Set(newTasks.map((t) => t.id));
  const existingIds = Array.from(tasks$.get().keys());

  existingIds.forEach((id) => {
    if (!newTaskIds.has(id)) {
      tasks$.delete(id);
    }
  });

  // Update or add new tasks
  newTasks.forEach((task) => {
    const existing = tasks$.get(task.id);
    if (existing) {
      existing.data.set(task);
    } else {
      initTask(task);
    }
  });
}

// Query hooks
export function useTasksQuery() {
  const query = useQuery({
    queryKey: ['tasks', { includeArchived: showArchived$.get() }],
    queryFn: () => taskApi.listTasks(showArchived$.get()),
  });

  // Update store when data changes
  React.useEffect(() => {
    if (query.data) {
      updateTasks(query.data);
    }
  }, [query.data]);

  return query;
}

export function useTaskQuery(taskId: string | null) {
  const query = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => taskApi.getTask(taskId!),
    enabled: !!taskId,
  });

  // Update store when data changes
  React.useEffect(() => {
    if (query.data) {
      const existing = tasks$.get(query.data.id);
      if (existing) {
        existing.data.set(query.data);
      } else {
        initTask(query.data);
      }
    }
  }, [query.data]);

  return query;
}

export function useArchiveTaskMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskId: string) => taskApi.archiveTask(taskId),
    onMutate: async (taskId) => {
      // Optimistic update
      updateTask(taskId, { archived: true });
      setTaskUpdating(taskId, true);
    },
    onSettled: (_data, _error, taskId) => {
      setTaskUpdating(taskId, false);
      // Invalidate queries to refetch
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['task', taskId] });
    },
  });
}

export function useUnarchiveTaskMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskId: string) => taskApi.unarchiveTask(taskId),
    onMutate: async (taskId) => {
      // Optimistic update
      updateTask(taskId, { archived: false });
      setTaskUpdating(taskId, true);
    },
    onSettled: (_data, _error, taskId) => {
      setTaskUpdating(taskId, false);
      // Invalidate queries to refetch
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['task', taskId] });
    },
  });
}

export function useCreateTaskMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (request: CreateTaskRequest) => taskApi.createTask(request),
    onSuccess: (newTask: Task) => {
      // Add to store
      initTask(newTask);
      // Set as selected
      selectedTask$.set(newTask.id);
      // Invalidate queries
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
}
