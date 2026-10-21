import React from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { observable } from '@legendapp/state';
import {
  showArchived$,
  useArchiveTaskMutation,
  useCreateTaskMutation,
  useTaskQuery,
  useTasksQuery,
  useUnarchiveTaskMutation,
} from '../tasks';
import type { Task } from '@/types/task';

function owner(baseUrl: string, authHeader: string) {
  return { api: { baseUrl, authHeader }, isConnected$: observable(true) };
}

let mockOwner = owner('https://a.invalid', 'Bearer credential-a');
jest.mock('@/contexts/ApiContext', () => ({ useApi: () => mockOwner }));
jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => false,
  getApiBaseUrl: () => mockOwner.api.baseUrl,
  getAuthHeader: () => mockOwner.api.authHeader,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function task(content: string, id = 'shared-id'): Task {
  return {
    id,
    content,
    created_at: '2026-10-21T00:00:00Z',
    status: 'pending',
    target_type: 'stdout',
    conversation_ids: [],
    metadata: {},
    archived: false,
  };
}

type PendingRequest = {
  url: string;
  options: RequestInit;
  respond: (body?: unknown) => void;
};
let requests: PendingRequest[];
const clients: QueryClient[] = [];
const originalFetch = global.fetch;

function setup(onMutate?: () => Promise<void>) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onMutate }),
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

beforeEach(() => {
  mockOwner = owner('https://a.invalid', 'Bearer credential-a');
  showArchived$.set(false);
  requests = [];
  global.fetch = jest.fn((input: RequestInfo | URL, options: RequestInit = {}) => {
    const response = deferred<Response>();
    requests.push({
      url: String(input),
      options,
      respond: (body) => response.resolve({ ok: true, json: async () => body } as Response),
    });
    return response.promise;
  });
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  global.fetch = originalFetch;
});

it.each([
  ['server, same task ID', 'https://b.invalid', 'shared-id'],
  ['server, distinct task ID', 'https://b.invalid', 'b-only'],
  ['credentials at the same server', 'https://a.invalid', 'shared-id'],
])(
  'isolates list and detail while switching %s, and restores the original owner',
  async (_, url, id) => {
    const a = mockOwner;
    const { client, wrapper } = setup();
    const mounted = renderHook(
      ({ taskId }) => ({
        list: useTasksQuery(),
        detail: useTaskQuery(taskId),
      }),
      { initialProps: { taskId: 'shared-id' }, wrapper }
    );
    await waitFor(() => expect(requests.length).toBe(2));
    act(() => {
      requests[0].respond({ tasks: [task('private A')] });
      requests[1].respond(task('private A'));
    });
    await waitFor(() => expect(mounted.result.current.detail.data?.content).toBe('private A'));
    await waitFor(() => expect(mounted.result.current.list.data?.[0]?.content).toBe('private A'));

    mockOwner = owner(url, 'Bearer credential-b');
    mounted.rerender({ taskId: id });
    expect(mounted.result.current.list.data ?? []).toEqual([]);
    expect(mounted.result.current.detail.data).toBeUndefined();
    await waitFor(() => expect(requests.length).toBe(4));
    expect(requests[2].url).toBe(`${url}/api/v2/tasks`);
    expect(requests[3].url).toBe(`${url}/api/v2/tasks/${id}`);
    expect(
      requests.slice(2).map((r) => new Headers(r.options.headers).get('Authorization'))
    ).toEqual(['Bearer credential-b', 'Bearer credential-b']);
    act(() => {
      requests[2].respond({ tasks: [task('private B', id)] });
      requests[3].respond(task('private B', id));
    });
    await waitFor(() => expect(mounted.result.current.detail.data?.content).toBe('private B'));

    mockOwner = a;
    mounted.rerender({ taskId: 'shared-id' });
    expect(mounted.result.current.list.data?.[0]?.content).toBe('private A');
    expect(mounted.result.current.detail.data?.content).toBe('private A');
    expect(requests.length).toBe(4);
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((q) => q.queryKey)
      )
    ).not.toContain('credential-');
  }
);

it('keeps delayed A list and detail responses out of B, then caches them for A', async () => {
  const a = mockOwner;
  const { wrapper } = setup();
  const mounted = renderHook(() => ({ list: useTasksQuery(), detail: useTaskQuery('shared-id') }), {
    wrapper,
  });
  await waitFor(() => expect(requests.length).toBe(2));
  mockOwner = owner('https://b.invalid', 'Bearer credential-b');
  mounted.rerender();
  await waitFor(() => expect(requests.length).toBe(4));
  act(() => {
    requests[2].respond({ tasks: [task('B')] });
    requests[3].respond(task('B'));
  });
  await waitFor(() => expect(mounted.result.current.detail.data?.content).toBe('B'));
  await act(async () => {
    requests[0].respond({ tasks: [task('late A')] });
    requests[1].respond(task('late A'));
  });
  expect(mounted.result.current.list.data?.[0]?.content).toBe('B');
  expect(mounted.result.current.detail.data?.content).toBe('B');
  mockOwner = a;
  mounted.rerender();
  await waitFor(() => expect(mounted.result.current.list.data?.[0]?.content).toBe('late A'));
  expect(mounted.result.current.detail.data?.content).toBe('late A');
});

it.each(['archive', 'unarchive', 'create'] as const)(
  'binds %s before async onMutate, and settlement invalidates only that owner',
  async (operation) => {
    const a = mockOwner;
    const barrier = deferred<void>();
    const { client, wrapper } = setup(() => barrier.promise);
    const mounted = renderHook(
      () => ({
        list: useTasksQuery(),
        detail: useTaskQuery('shared-id'),
        archive: useArchiveTaskMutation(),
        unarchive: useUnarchiveTaskMutation(),
        create: useCreateTaskMutation(),
      }),
      { wrapper }
    );
    await waitFor(() => expect(requests.length).toBe(2));
    act(() => {
      requests[0].respond({ tasks: [task('A')] });
      requests[1].respond(task('A'));
    });
    await waitFor(() => expect(mounted.result.current.detail.data?.content).toBe('A'));
    await waitFor(() => expect(mounted.result.current.list.data?.[0]?.content).toBe('A'));
    const aQueries = client.getQueryCache().getAll();
    let pending!: Promise<unknown>;
    act(() => {
      pending =
        operation === 'create'
          ? mounted.result.current.create.mutateAsync({ content: 'new A task' })
          : mounted.result.current[operation].mutateAsync('shared-id');
    });
    mockOwner = owner('https://b.invalid', 'Bearer credential-b');
    mounted.rerender();
    await waitFor(() => expect(requests.length).toBe(4));
    act(() => {
      requests[2].respond({ tasks: [task('B')] });
      requests[3].respond(task('B'));
    });
    await waitFor(() => expect(mounted.result.current.detail.data?.content).toBe('B'));
    const bQueries = client
      .getQueryCache()
      .getAll()
      .filter((q) => !aQueries.includes(q));
    await act(async () => {
      barrier.resolve();
    });
    await waitFor(() => expect(requests.length).toBe(5));
    const post = requests[4];
    expect(post.url).toBe(
      `https://a.invalid/api/v2/tasks${operation === 'create' ? '' : `/shared-id/${operation}`}`
    );
    expect(post.options.method).toBe('POST');
    expect(new Headers(post.options.headers).get('Authorization')).toBe('Bearer credential-a');
    if (operation === 'create')
      expect(JSON.parse(post.options.body as string)).toEqual({ content: 'new A task' });
    await act(async () => {
      post.respond(task('new A task', 'new-id'));
      await pending;
    });
    expect(requests.length).toBe(5);
    expect(bQueries).toHaveLength(2);
    expect(bQueries.every((q) => !q.state.isInvalidated)).toBe(true);
    expect(aQueries.find((q) => Array.isArray(q.state.data))?.state.isInvalidated).toBe(true);
    if (operation !== 'create') expect(aQueries.every((q) => q.state.isInvalidated)).toBe(true);
    expect(mounted.result.current.list.data?.[0]?.content).toBe('B');
    expect(mounted.result.current.detail.data?.content).toBe('B');
    mockOwner = a;
    mounted.rerender();
    await waitFor(() => expect(requests.length).toBeGreaterThan(5));
    expect(requests.slice(5).every((r) => r.url.startsWith('https://a.invalid/'))).toBe(true);
  }
);

it('disables detail requests when disconnected and observes the archived toggle', async () => {
  mockOwner.isConnected$.set(false);
  const { wrapper } = setup();
  const mounted = renderHook(() => ({ list: useTasksQuery(), detail: useTaskQuery('shared-id') }), {
    wrapper,
  });
  expect(requests.length).toBe(0);
  act(() => {
    mockOwner.isConnected$.set(true);
  });
  await waitFor(() => expect(requests.length).toBe(2));
  act(() => {
    requests[0].respond({ tasks: [task('active')] });
    requests[1].respond(task('active'));
  });
  await waitFor(() => expect(mounted.result.current.list.data?.[0]?.content).toBe('active'));
  act(() => {
    showArchived$.set(true);
  });
  await waitFor(() => expect(requests.length).toBe(3));
  expect(requests[2].url).toBe('https://a.invalid/api/v2/tasks?include_archived=true');
  act(() => {
    requests[2].respond({
      tasks: [task('active'), { ...task('archived', 'old'), archived: true }],
    });
  });
  await waitFor(() => expect(mounted.result.current.list.data).toHaveLength(2));
});
