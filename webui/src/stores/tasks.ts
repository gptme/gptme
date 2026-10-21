import { useMemo } from 'react';
import { observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { taskApi } from '@/utils/taskApi';
import { getClientGeneration } from '@/utils/api';
import { useApi } from '@/contexts/ApiContext';
import { isDemoMode } from '@/utils/connectionConfig';
import type { CreateTaskRequest } from '@/types/task';

// Task data lives only in React Query; a second ID-only store can mix servers.
export const showArchived$ = observable(false);

function useTaskOwner() {
  const { api } = useApi();
  return useMemo(
    () => ({
      // The pool replaces clients on endpoint or credential changes. This key
      // scopes data without putting credentials into the query cache.
      key: getClientGeneration(api),
      connection: { baseUrl: api.baseUrl, authHeader: api.authHeader },
    }),
    [api]
  );
}

type TaskOwner = ReturnType<typeof useTaskOwner>;
const tasksKey = (owner: TaskOwner) => ['tasks', owner.key] as const;
const taskKey = (owner: TaskOwner, taskId: string | null) => ['task', owner.key, taskId] as const;

export function useTasksQuery() {
  const { isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const includeArchived = use$(showArchived$);
  const owner = useTaskOwner();
  return useQuery({
    queryKey: [...tasksKey(owner), { includeArchived }],
    queryFn: () => taskApi.listTasks(includeArchived, owner.connection),
    enabled: isConnected && !isDemoMode(),
  });
}

export function useTaskQuery(taskId: string | null) {
  const { isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const owner = useTaskOwner();
  return useQuery({
    queryKey: taskKey(owner, taskId),
    queryFn: () => taskApi.getTask(taskId!, owner.connection),
    enabled: !!taskId && isConnected && !isDemoMode(),
  });
}

function useTaskArchiveMutation(archived: boolean) {
  const queryClient = useQueryClient();
  const owner = useTaskOwner();
  const mutation = useMutation({
    // Capture the owner in variables: React Query can replace callback options
    // during a rerender while the mutation is pending.
    mutationFn: ({ taskId, owner }: { taskId: string; owner: TaskOwner }) =>
      archived
        ? taskApi.archiveTask(taskId, owner.connection)
        : taskApi.unarchiveTask(taskId, owner.connection),
    onSettled: (_data, _error, { taskId, owner }) => {
      void queryClient.invalidateQueries({ queryKey: tasksKey(owner) });
      void queryClient.invalidateQueries({ queryKey: taskKey(owner, taskId) });
    },
  });
  return {
    ...mutation,
    mutate: (taskId: string) => mutation.mutate({ taskId, owner }),
    mutateAsync: (taskId: string) => mutation.mutateAsync({ taskId, owner }),
  };
}

export function useArchiveTaskMutation() {
  return useTaskArchiveMutation(true);
}

export function useUnarchiveTaskMutation() {
  return useTaskArchiveMutation(false);
}

export function useCreateTaskMutation() {
  const queryClient = useQueryClient();
  const owner = useTaskOwner();
  const mutation = useMutation({
    mutationFn: ({ request, owner }: { request: CreateTaskRequest; owner: TaskOwner }) =>
      taskApi.createTask(request, owner.connection),
    onSuccess: (newTask, { owner }) => {
      queryClient.setQueryData(taskKey(owner, newTask.id), newTask);
      void queryClient.invalidateQueries({ queryKey: tasksKey(owner) });
    },
  });
  return {
    ...mutation,
    mutate: (request: CreateTaskRequest) => mutation.mutate({ request, owner }),
    mutateAsync: (request: CreateTaskRequest) => mutation.mutateAsync({ request, owner }),
  };
}
