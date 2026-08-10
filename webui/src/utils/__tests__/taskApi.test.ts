import { taskApi } from '../taskApi';

const mockFetch = jest.fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>();

const errorResponse = (status: number, statusText: string, error?: string): Response =>
  ({
    ok: false,
    status,
    statusText,
    json: async () => (error ? { error } : {}),
  }) as Response;

describe('taskApi error messages', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    mockFetch.mockReset();
    global.fetch = mockFetch;
  });

  afterAll(() => {
    global.fetch = originalFetch;
  });

  it.each([
    ['createTask', () => taskApi.createTask({ content: 'test' }), 'Failed to create task'],
    [
      'updateTask',
      () => taskApi.updateTask('task-1', { content: 'test' }),
      'Failed to update task',
    ],
    ['archiveTask', () => taskApi.archiveTask('task-1'), 'Failed to archive task'],
    ['unarchiveTask', () => taskApi.unarchiveTask('task-1'), 'Failed to unarchive task'],
    ['continueTask', () => taskApi.continueTask('task-1'), 'Failed to continue task'],
  ])('includes status and structured server error for %s', async (_name, request, prefix) => {
    mockFetch.mockResolvedValueOnce(errorResponse(409, 'Conflict', 'task state conflict'));

    await expect(request()).rejects.toThrow(`${prefix}: 409 task state conflict`);
  });

  it('includes status in the dedicated task-not-found error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(404, 'Not Found'));

    await expect(taskApi.getTask('missing-task')).rejects.toThrow(
      'Task not found: missing-task (404 Not Found)'
    );
  });
});
