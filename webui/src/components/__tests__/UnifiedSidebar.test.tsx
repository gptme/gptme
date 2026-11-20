import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { UnifiedSidebar } from '../UnifiedSidebar';

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: jest.fn(() => false),
}));

// Resolve the mock through the registry: the factory above is hoisted above
// this const, and importing UnifiedSidebar now pulls in serverClients, which
// calls isDemoMode() during module evaluation. A factory closing over a
// later-initialized const would hit the temporal dead zone there.
const mockIsDemoMode = jest.requireMock('@/utils/connectionConfig').isDemoMode as jest.Mock;

const mockGetExternalSessions = jest.fn();
const mockGetServerInfo = jest.fn(async () => ({}));

jest.mock('@/contexts/ApiContext', () => {
  const { observable: createObservable } = jest.requireActual('@legendapp/state');
  const isConnected$ = createObservable(true);
  return {
    useApi: () => ({
      api: {
        getExternalSessions: (...args: unknown[]) => mockGetExternalSessions(...args),
        getServerInfo: () => mockGetServerInfo(),
      },
      connectionConfig: { baseUrl: 'demo://offline' },
      isConnected$,
    }),
  };
});

const selectedConversationId$ = observable<string | null>(null);

const renderTaskSidebar = (
  initialEntry = '/tasks',
  props: Partial<ComponentProps<typeof UnifiedSidebar>> = {}
) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <UnifiedSidebar
          conversations={[]}
          selectedConversationId$={selectedConversationId$}
          onSelectConversation={jest.fn()}
          fetchNextPage={jest.fn()}
          tasks={[]}
          onSelectTask={jest.fn()}
          onCreateTask={jest.fn()}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { ...result, queryClient };
};

describe('Chats header search and tooltips', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
    mockGetServerInfo.mockResolvedValue({});
  });

  it('opens and focuses search with one click, even with no conversations', async () => {
    const user = userEvent.setup();
    renderTaskSidebar('/chat');
    expect(screen.queryByRole('textbox', { name: 'Search conversations' })).not.toBeInTheDocument();
    const search = screen.getByRole('button', { name: 'Search conversations' });
    await user.click(search);
    const input = screen.getByRole('textbox', { name: 'Search conversations' });
    expect(input).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('textbox', { name: 'Search conversations' })).not.toBeInTheDocument();
    expect(search).toHaveFocus();
  });

  it('keeps an active URL filter visible instead of hiding it', async () => {
    const user = userEvent.setup();
    renderTaskSidebar('/chat?search=alpha');
    const input = screen.getByRole('textbox', { name: 'Search conversations' });
    expect(input).toHaveValue('alpha');
    await user.click(input);
    await user.keyboard('{Escape}');
    expect(input).toBeVisible();
    expect(input).toHaveValue('alpha');
  });

  it('keeps the cleared search focused after dismissing an active filter', async () => {
    const user = userEvent.setup();
    renderTaskSidebar('/chat?search=alpha');
    const input = screen.getByRole('textbox', { name: 'Search conversations' });
    await user.click(input);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Clear conversation search' }));
    expect(input).toHaveValue('');
    expect(input).toHaveFocus();
  });

  it('does not unmount a URL-loaded search while deleting its last character', async () => {
    const user = userEvent.setup();
    renderTaskSidebar('/chat?search=a');
    const input = screen.getByRole('textbox', { name: 'Search conversations' });
    await user.click(input);
    await user.keyboard('{Backspace}');
    expect(input).toHaveFocus();
    expect(input).toHaveValue('');
  });

  it('keeps the unavailable import action discoverable while disconnected', async () => {
    const { isConnected$ } = jest.requireMock('@/contexts/ApiContext').useApi();
    isConnected$.set(false);
    try {
      const user = userEvent.setup();
      const { container } = renderTaskSidebar('/chat');
      const fileInput = container.querySelector('input[type="file"]')!;
      const click = jest.spyOn(fileInput as HTMLInputElement, 'click');
      const button = screen.getByRole('button', { name: 'Import conversation JSON' });
      act(() => button.focus());
      expect(await screen.findByRole('tooltip')).toHaveTextContent(
        'Connect to import conversation JSON'
      );
      await user.keyboard('{Enter}');
      expect(click).not.toHaveBeenCalled();
    } finally {
      act(() => isConnected$.set(true));
    }
  });

  it('opens the disclosed search with the existing Alt+F and slash shortcuts', () => {
    renderTaskSidebar('/chat');
    fireEvent.keyDown(window, { key: 'f', altKey: true });
    const input = screen.getByRole('textbox', { name: 'Search conversations' });
    expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: 'Escape' });
    fireEvent.keyDown(window, { key: '/' });
    expect(screen.getByRole('textbox', { name: 'Search conversations' })).toHaveFocus();
  });

  it.each([
    ['Alt+F', { key: 'f', altKey: true }],
    ['slash', { key: '/' }],
  ])('selects the existing filter with %s after dismissing search', async (_label, shortcut) => {
    const user = userEvent.setup();
    renderTaskSidebar('/chat?search=alpha');
    const input = screen.getByRole('textbox', { name: 'Search conversations' }) as HTMLInputElement;
    await user.click(input);
    input.setSelectionRange(5, 5);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('button', { name: 'Search conversations' })).toHaveFocus();
    expect(input).toBeVisible();
    fireEvent.keyDown(window, shortcut);
    expect(input).toHaveFocus();
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(5);
  });

  it.each([
    ['Search conversations', 'Search conversations (/ or Alt+F); command palette: Cmd+K / Ctrl+K'],
    ['Import conversation JSON', 'Import conversation JSON'],
    ['New conversation', 'New conversation'],
  ])('shows a tooltip on keyboard focus and hover for %s', async (label, tooltip) => {
    const user = userEvent.setup();
    renderTaskSidebar('/chat');
    const button = screen.getByRole('button', { name: label });
    act(() => button.focus());
    expect(await screen.findByRole('tooltip')).toHaveTextContent(tooltip);
    await user.keyboard('{Escape}');
    act(() => button.blur());
    await user.hover(button);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(tooltip);
  });
});

const SERVER_INFO_KEY = ['server-info', 'demo://offline'];

const waitForServerInfo = async (queryClient: QueryClient) => {
  await waitFor(() => expect(queryClient.getQueryState(SERVER_INFO_KEY)?.status).toBe('success'));
};

describe('UnifiedSidebar task creation', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
  });

  it('asks for a server connection instead of reporting no tasks while disconnected', () => {
    const { isConnected$ } = jest.requireMock('@/contexts/ApiContext').useApi();
    isConnected$.set(false);
    try {
      renderTaskSidebar();

      expect(screen.getByText('Connect to a gptme server to view tasks.')).toBeInTheDocument();
      expect(screen.queryByText('No tasks yet')).not.toBeInTheDocument();
    } finally {
      act(() => isConnected$.set(true));
    }
  });

  it.each([false, true])(
    'hides cached tasks and errors while disconnected (error=%s)',
    (tasksError) => {
      const { isConnected$ } = jest.requireMock('@/contexts/ApiContext').useApi();
      isConnected$.set(false);
      try {
        renderTaskSidebar('/tasks', {
          tasks: [
            {
              id: 'cached-task',
              content: 'Cached task content',
              created_at: '2026-11-17T12:00:00Z',
              status: 'pending',
              target_type: 'stdout',
              conversation_ids: [],
              metadata: {},
              archived: false,
            },
          ],
          tasksError,
          onTasksRetry: jest.fn(),
        });

        expect(screen.getByText('Connect to a gptme server to view tasks.')).toBeInTheDocument();
        expect(screen.queryByText('Cached task content')).not.toBeInTheDocument();
        expect(screen.queryByText('Failed to load tasks')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
      } finally {
        act(() => isConnected$.set(true));
      }
    }
  );

  it('offers task creation with a live server', () => {
    renderTaskSidebar();

    expect(screen.getByRole('button', { name: 'Create task' })).toBeInTheDocument();
  });

  it('does not offer a backend-only create action in offline demo mode', () => {
    mockIsDemoMode.mockReturnValue(true);
    renderTaskSidebar();

    expect(screen.queryByRole('button', { name: 'Create task' })).not.toBeInTheDocument();
    expect(screen.getByText('Task creation requires a live gptme server.')).toBeInTheDocument();
  });
});

describe('UnifiedSidebar external sessions capability gate', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
    mockGetExternalSessions.mockReset();
    mockGetServerInfo.mockReset();
    mockGetExternalSessions.mockResolvedValue([]);
  });

  it('does not request the catalog when the server lacks the capability', async () => {
    mockGetServerInfo.mockResolvedValue({
      version: '0.30.0',
      capabilities: { external_session_catalog: false, external_session_transcript: false },
    });
    const { queryClient } = renderTaskSidebar('/chat');

    await waitForServerInfo(queryClient);
    expect(mockGetServerInfo).toHaveBeenCalledTimes(1);
    expect(mockGetExternalSessions).not.toHaveBeenCalled();
  });

  it('does not request the catalog when the server reports no capabilities', async () => {
    mockGetServerInfo.mockResolvedValue({ version: '0.29.0' });
    const { queryClient } = renderTaskSidebar('/chat');

    await waitForServerInfo(queryClient);
    expect(mockGetServerInfo).toHaveBeenCalledTimes(1);
    expect(mockGetExternalSessions).not.toHaveBeenCalled();
  });

  it('requests the catalog once the server advertises the capability', async () => {
    mockGetServerInfo.mockResolvedValue({
      version: '0.30.0',
      capabilities: { external_session_catalog: true, external_session_transcript: true },
    });
    renderTaskSidebar('/chat');

    await waitFor(() => expect(mockGetExternalSessions).toHaveBeenCalledWith(7));
  });
});
