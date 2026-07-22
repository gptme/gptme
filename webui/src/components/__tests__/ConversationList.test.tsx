import { render, screen, fireEvent, act } from '@testing-library/react';
import { ConversationList } from '../ConversationList';
import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import type { ConversationSummary } from '@/types/conversation';
import { TooltipProvider } from '@/components/ui/tooltip';
import { MemoryRouter } from 'react-router-dom';
import { conversations$ } from '@/stores/conversations';

// Mock the ApiContext
const mockDeleteConversation = jest.fn().mockResolvedValue(undefined);
const mockGetChatConfig = jest.fn().mockResolvedValue({
  chat: {
    name: 'Test Chat',
    model: null,
    tools: null,
    tool_format: null,
    stream: true,
    interactive: true,
    workspace: '/tmp',
  },
  env: {},
  mcp: { servers: [] },
});
const mockUpdateChatConfig = jest.fn().mockResolvedValue(undefined);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      deleteConversation: mockDeleteConversation,
      getChatConfig: mockGetChatConfig,
      updateChatConfig: mockUpdateChatConfig,
    },
    connectionConfig: { baseUrl: 'http://localhost:5700', apiKey: '' },
    isConnected$: observable(true),
  }),
}));

// Mock sonner toast
jest.mock('sonner', () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}));

// Mock tanstack query
jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({
    invalidateQueries: jest.fn(),
  }),
}));

// Mock democonversations
jest.mock('@/democonversations', () => ({
  demoConversations: [{ id: 'demo-1', name: 'Demo Chat' }],
  getDemoMessages: () => [],
}));

// Mock conversations store
jest.mock('@/stores/conversations', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable } = require('@legendapp/state');
  const store = observable(new Map());
  return {
    conversations$: store,
    selectedConversation$: observable('demo-1'),
  };
});

const createConversation = (overrides: Partial<ConversationSummary> = {}): ConversationSummary => ({
  id: 'test-conv-1',
  name: 'Test Conversation',
  messages: 5,
  modified: Date.now() / 1000,
  readonly: false,
  ...overrides,
});

// Helper to render with required providers
const renderWithProviders = (ui: React.ReactElement, { initialSearch = '' } = {}) => {
  const initialEntries = initialSearch ? [`/?search=${encodeURIComponent(initialSearch)}`] : ['/'];
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <TooltipProvider>{ui}</TooltipProvider>
    </MemoryRouter>
  );
};

describe('ConversationList', () => {
  const defaultProps = {
    conversations: [createConversation()],
    onSelect: jest.fn(),
    fetchNextPage: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
    conversations$.set(new Map());
  });

  it('renders conversation items', () => {
    renderWithProviders(<ConversationList {...defaultProps} />);
    expect(screen.getByTestId('conversation-title')).toBeInTheDocument();
  });

  it('renders conversation list container', () => {
    renderWithProviders(<ConversationList {...defaultProps} conversations={[]} />);
    expect(screen.getByTestId('conversation-list')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    renderWithProviders(<ConversationList {...defaultProps} isLoading={true} />);
    expect(screen.getByText('Loading conversations...')).toBeInTheDocument();
  });

  it('shows error state with retry button', () => {
    const onRetry = jest.fn();
    renderWithProviders(
      <ConversationList
        {...defaultProps}
        isError={true}
        error={new Error('Network error')}
        onRetry={onRetry}
      />
    );
    expect(screen.getByText('Failed to load conversations')).toBeInTheDocument();
    expect(screen.getByText('Network error')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Retry'));
    expect(onRetry).toHaveBeenCalled();
  });

  it('calls onSelect when conversation is clicked', () => {
    renderWithProviders(<ConversationList {...defaultProps} />);
    const item = screen.getByTestId('conversation-title');
    fireEvent.click(item);
    expect(defaultProps.onSelect).toHaveBeenCalledWith('test-conv-1', undefined);
  });

  it('strips date prefix from conversation id', () => {
    const conv = createConversation({ id: '2026-03-29-my-chat', name: '' });
    renderWithProviders(<ConversationList {...defaultProps} conversations={[conv]} />);
    expect(screen.getByTestId('conversation-title')).toHaveTextContent('my-chat');
  });

  it('renders multiple conversations', () => {
    const convs = [
      createConversation({ id: 'conv-1', name: 'First' }),
      createConversation({ id: 'conv-2', name: 'Second' }),
    ];
    renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />);
    const titles = screen.getAllByTestId('conversation-title');
    expect(titles).toHaveLength(2);
  });

  it('uses list summary counts instead of scanning loaded conversation logs', () => {
    const conv = createConversation({ id: 'loaded-conv', name: 'Summary Name', messages: 5 });
    conversations$.set('loaded-conv', {
      data: {
        id: 'loaded-conv',
        name: 'Loaded Name',
        log: Array.from({ length: 100 }, (_, index) => ({
          role: 'user',
          content: `message ${index}`,
        })),
        logfile: 'loaded-conv',
        branches: {},
        workspace: '/tmp',
      },
      isGenerating: false,
      isConnected: false,
      connectionStatus: 'disconnected',
      reconnectAttempt: null,
      reconnectMaxAttempts: null,
      reconnectRetryInMs: null,
      reconnectRetryStartedAt: null,
      connectionError: null,
      loadError: null,
      pendingTool: null,
      executingTool: null,
      lastCompletedTool: null,
      showInitialSystem: false,
      chatConfig: null,
      needsInitialStep: false,
      currentBranch: 'main',
      logOffset: 0,
      hasMoreBefore: false,
      isWindowHydrated: true,
    });

    renderWithProviders(<ConversationList {...defaultProps} conversations={[conv]} />);

    expect(screen.getByTestId('conversation-title')).toHaveTextContent('Loaded Name');
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.queryByText('100')).not.toBeInTheDocument();
  });

  it('filters conversations by name', () => {
    const convs = [
      createConversation({ id: 'conv-1', name: 'Alpha Project' }),
      createConversation({ id: 'conv-2', name: 'Beta Notes' }),
    ];
    renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />);
    fireEvent.change(screen.getByLabelText('Search conversations'), {
      target: { value: 'alpha' },
    });

    // getByText can't find text split across <mark> nodes; use testid + textContent instead
    const titles = screen.getAllByTestId('conversation-title');
    expect(titles).toHaveLength(1);
    expect(titles[0]).toHaveTextContent('Alpha Project');
    expect(screen.queryByText('Beta Notes')).not.toBeInTheDocument();
  });

  it('shows an empty state when no conversations match the filter', () => {
    renderWithProviders(<ConversationList {...defaultProps} />);
    fireEvent.change(screen.getByLabelText('Search conversations'), {
      target: { value: 'missing' },
    });

    expect(screen.getByText('No conversations match your search.')).toBeInTheDocument();
    expect(screen.queryByTestId('conversation-title')).not.toBeInTheDocument();
  });

  it('clears the conversation filter', () => {
    const convs = [
      createConversation({ id: 'conv-1', name: 'Alpha Project' }),
      createConversation({ id: 'conv-2', name: 'Beta Notes' }),
    ];
    renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />);
    const searchInput = screen.getByLabelText('Search conversations');

    fireEvent.change(searchInput, { target: { value: 'alpha' } });
    fireEvent.click(screen.getByLabelText('Clear conversation search'));

    expect(searchInput).toHaveValue('');
    expect(screen.getByText('Alpha Project')).toBeInTheDocument();
    expect(screen.getByText('Beta Notes')).toBeInTheDocument();
  });

  it('focuses the conversation search with Alt+F', () => {
    renderWithProviders(<ConversationList {...defaultProps} />);
    const searchInput = screen.getByLabelText('Search conversations');

    fireEvent.keyDown(window, { key: 'f', altKey: true });

    expect(searchInput).toHaveFocus();
  });

  it('focuses the conversation search with /', () => {
    renderWithProviders(<ConversationList {...defaultProps} />);
    const searchInput = screen.getByLabelText('Search conversations');

    fireEvent.keyDown(window, { key: '/' });

    expect(searchInput).toHaveFocus();
  });

  it('does not focus search when / is pressed while a different input is focused', () => {
    renderWithProviders(<ConversationList {...defaultProps} />);
    const searchInput = screen.getByLabelText('Search conversations');

    // Simulate a message input (not the search box) being focused
    const messageInput = document.createElement('input');
    document.body.appendChild(messageInput);
    messageInput.focus();
    expect(document.activeElement).toBe(messageInput);

    fireEvent.keyDown(window, { key: '/' });

    // Guard must fire: search should not steal focus
    expect(searchInput).not.toHaveFocus();
    expect(messageInput).toHaveFocus();

    document.body.removeChild(messageInput);
  });

  it('shows end-of-list message when no more pages', () => {
    renderWithProviders(<ConversationList {...defaultProps} hasNextPage={false} />);
    expect(screen.getByText("You've reached the end of your conversations.")).toBeInTheDocument();
  });

  it('shows fetching indicator for infinite scroll', () => {
    renderWithProviders(<ConversationList {...defaultProps} isFetching={true} />);
    expect(screen.getByText('Loading more conversations...')).toBeInTheDocument();
  });

  it('shows last message preview', () => {
    const conv = createConversation({
      last_message_preview: 'Hello world',
      last_message_role: 'user',
    });
    renderWithProviders(<ConversationList {...defaultProps} conversations={[conv]} />);
    expect(screen.getByText(/Hello world/)).toBeInTheDocument();
  });

  it('renders readonly conversation with lock icon', () => {
    const conv = createConversation({ readonly: true });
    const { container } = renderWithProviders(
      <ConversationList {...defaultProps} conversations={[conv]} />
    );
    // Lock icon is rendered for readonly conversations
    const lockIcon = container.querySelector('.lucide-lock');
    expect(lockIcon).toBeInTheDocument();
  });

  it('shows server label when showServerLabels is true', () => {
    const conv = createConversation({ serverName: 'server-1' });
    renderWithProviders(
      <ConversationList {...defaultProps} conversations={[conv]} showServerLabels={true} />
    );
    expect(screen.getByText('server-1')).toBeInTheDocument();
  });

  describe('URL search state persistence', () => {
    it('populates filter from ?search= URL param on mount', () => {
      const convs = [
        createConversation({ id: 'conv-1', name: 'Alpha Project' }),
        createConversation({ id: 'conv-2', name: 'Beta Notes' }),
      ];
      renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />, {
        initialSearch: 'alpha',
      });

      expect(screen.getByLabelText('Search conversations')).toHaveValue('alpha');
      // getByText fails when highlightText splits 'Alpha' into a <mark> element;
      // toHaveTextContent checks the full textContent including child nodes.
      expect(screen.getByTestId('conversation-title')).toHaveTextContent('Alpha Project');
      expect(screen.queryByText('Beta Notes')).not.toBeInTheDocument();
    });

    it('clears URL search when clear button is clicked', async () => {
      jest.useFakeTimers();
      const convs = [
        createConversation({ id: 'conv-1', name: 'Alpha Project' }),
        createConversation({ id: 'conv-2', name: 'Beta Notes' }),
      ];
      renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />, {
        initialSearch: 'alpha',
      });

      // Verify initial filter active
      expect(screen.queryByText('Beta Notes')).not.toBeInTheDocument();

      fireEvent.click(screen.getByLabelText('Clear conversation search'));

      // Local state clears immediately
      expect(screen.getByLabelText('Search conversations')).toHaveValue('');
      expect(screen.getByText('Beta Notes')).toBeInTheDocument();

      // Flush the 300ms debounce — verifies the URL param write fires and doesn't corrupt state
      act(() => jest.runAllTimers());
      expect(screen.getByLabelText('Search conversations')).toHaveValue('');
      expect(screen.getByText('Beta Notes')).toBeInTheDocument();

      jest.useRealTimers();
    });
  });

  // Note: Radix UI ContextMenu requires pointer events that JSDOM doesn't fully support.
  // Context menu functionality (rename, delete, export) is tested via the ConversationSettings
  // component tests and manual testing. The context menu wraps existing functionality that
  // is already tested elsewhere (DeleteConversationConfirmationDialog, exportConversation utils).

  describe('keyboard accessibility', () => {
    // Resolve the clickable row (role="button") from the nested title element.
    const getRow = () => {
      const row = screen.getByTestId('conversation-title').closest('[role="button"]');
      expect(row).not.toBeNull();
      return row as HTMLElement;
    };

    it('exposes the conversation row as a focusable button', () => {
      renderWithProviders(<ConversationList {...defaultProps} />);
      const row = getRow();
      expect(row).toHaveAttribute('role', 'button');
      expect(row).toHaveAttribute('tabindex', '0');
    });

    it('selects the conversation on Enter', () => {
      renderWithProviders(<ConversationList {...defaultProps} />);
      const row = getRow();
      fireEvent.keyDown(row, { key: 'Enter' });
      expect(defaultProps.onSelect).toHaveBeenCalledWith('test-conv-1', undefined);
    });

    it('selects the conversation on Space', () => {
      renderWithProviders(<ConversationList {...defaultProps} />);
      const row = getRow();
      fireEvent.keyDown(row, { key: ' ' });
      expect(defaultProps.onSelect).toHaveBeenCalledWith('test-conv-1', undefined);
    });

    it('reflects selection state via aria-pressed', () => {
      const selectedId$ = observable<string | null>('test-conv-1');
      renderWithProviders(<ConversationList {...defaultProps} selectedId$={selectedId$} />);
      expect(getRow()).toHaveAttribute('aria-pressed', 'true');
    });

    it('does not select when Enter is pressed on a nested child element', () => {
      renderWithProviders(<ConversationList {...defaultProps} />);
      const title = screen.getByTestId('conversation-title');
      fireEvent.keyDown(title, { key: 'Enter' });
      expect(defaultProps.onSelect).not.toHaveBeenCalled();
    });

    it('does not select when Space is pressed on a nested child element', () => {
      renderWithProviders(<ConversationList {...defaultProps} />);
      const title = screen.getByTestId('conversation-title');
      fireEvent.keyDown(title, { key: ' ' });
      expect(defaultProps.onSelect).not.toHaveBeenCalled();
    });
  });

  describe('date group headers', () => {
    it('renders date group headers for conversations', () => {
      const now = Date.now() / 1000;
      const daysAgo = 40;
      const oldDate = new Date((now - 60 * 60 * 24 * daysAgo) * 1000);
      const expectedMonth = oldDate.toLocaleString('default', { month: 'long' });
      const convs = [
        createConversation({ id: 'today-conv', name: 'Today Chat', modified: now }),
        createConversation({
          id: 'old-conv',
          name: 'Old Chat',
          modified: now - 60 * 60 * 24 * daysAgo, // daysAgo days ago
        }),
      ];
      renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />);
      const headers = screen.getAllByTestId('date-group-header');
      expect(headers.length).toBeGreaterThanOrEqual(2);
      expect(headers[0]).toHaveTextContent('Today');
      // Monthly drill-down: "Older" group is broken into month names
      expect(headers[headers.length - 1]).toHaveTextContent(expectedMonth);
    });

    it('shows single group header when all conversations are from today', () => {
      const now = Date.now() / 1000;
      const convs = [
        createConversation({ id: 'conv-1', name: 'First', modified: now }),
        createConversation({ id: 'conv-2', name: 'Second', modified: now - 60 }),
      ];
      renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />);
      const headers = screen.getAllByTestId('date-group-header');
      expect(headers).toHaveLength(1);
      expect(headers[0]).toHaveTextContent('Today');
    });

    it('does not render date headers when loading', () => {
      renderWithProviders(<ConversationList {...defaultProps} isLoading={true} />);
      expect(screen.queryAllByTestId('date-group-header')).toHaveLength(0);
    });

    it('does not render date headers for empty conversation list', () => {
      renderWithProviders(<ConversationList {...defaultProps} conversations={[]} />);
      expect(screen.queryAllByTestId('date-group-header')).toHaveLength(0);
    });

    it('groups conversations across multiple date ranges', () => {
      const now = Date.now() / 1000;
      const convs = [
        createConversation({ id: 'c1', name: 'Now', modified: now }),
        createConversation({ id: 'c2', name: 'Yesterday', modified: now - 86400 }),
        createConversation({ id: 'c3', name: 'Last Week', modified: now - 86400 * 5 }),
      ];
      renderWithProviders(<ConversationList {...defaultProps} conversations={convs} />);
      const headers = screen.getAllByTestId('date-group-header');
      expect(headers).toHaveLength(3);
      expect(headers[0]).toHaveTextContent('Today');
      expect(headers[1]).toHaveTextContent('Yesterday');
      expect(headers[2]).toHaveTextContent('This Week');
    });
  });

  describe('external sessions toggle', () => {
    const externalSession = {
      id: 'ext-1',
      session_id: 'ext-session-1',
      harness: 'claude-code',
      session_name: 'My CC Session',
      project: 'bob',
      model: 'claude-sonnet-4-6',
      started_at: '2026-07-19T10:00:00Z',
      last_activity: '2026-07-19T10:30:00Z',
      capabilities: [],
      trajectory_path: '/tmp/traj.jsonl',
    };
    const onSelectExternal = jest.fn();

    beforeEach(() => {
      localStorage.removeItem('gptme:show-external-sessions');
    });

    it('does not show toggle button when no external sessions are provided', () => {
      renderWithProviders(
        <ConversationList {...defaultProps} onSelectExternal={onSelectExternal} />
      );
      expect(screen.queryByLabelText('Show external sessions')).not.toBeInTheDocument();
    });

    it('does not show toggle button when externalSessions is empty', () => {
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[]}
          onSelectExternal={onSelectExternal}
        />
      );
      expect(screen.queryByLabelText('Show external sessions')).not.toBeInTheDocument();
    });

    it('shows toggle button when external sessions are available', () => {
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      expect(screen.getByLabelText('Show external sessions')).toBeInTheDocument();
    });

    it('hides external sessions by default (toggle off)', () => {
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      expect(screen.queryByText('External Sessions')).not.toBeInTheDocument();
      expect(screen.queryByText('My CC Session')).not.toBeInTheDocument();
    });

    it('shows external sessions after clicking toggle', () => {
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      fireEvent.click(screen.getByLabelText('Show external sessions'));
      // External sessions are mixed inline with native conversations in the recent view —
      // no separate "External Sessions" header; items appear with harness badge in date groups.
      expect(screen.getByText('My CC Session')).toBeInTheDocument();
      expect(screen.getByText('CC')).toBeInTheDocument(); // harness badge
    });

    it('hides external sessions again after toggling twice', () => {
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      const btn = screen.getByLabelText('Show external sessions');
      fireEvent.click(btn);
      expect(screen.getByText('My CC Session')).toBeInTheDocument();
      fireEvent.click(screen.getByLabelText('Hide external sessions'));
      expect(screen.queryByText('My CC Session')).not.toBeInTheDocument();
    });

    it('persists toggle state to localStorage', () => {
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      expect(localStorage.getItem('gptme:show-external-sessions')).toBeNull();
      fireEvent.click(screen.getByLabelText('Show external sessions'));
      expect(localStorage.getItem('gptme:show-external-sessions')).toBe('true');
      fireEvent.click(screen.getByLabelText('Hide external sessions'));
      expect(localStorage.getItem('gptme:show-external-sessions')).toBe('false');
    });

    it('restores toggle state from localStorage', () => {
      localStorage.setItem('gptme:show-external-sessions', 'true');
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      expect(screen.getByText('My CC Session')).toBeInTheDocument();
      expect(screen.getByLabelText('Hide external sessions')).toBeInTheDocument();
    });

    it('calls onSelectExternal when an external session is clicked', () => {
      localStorage.setItem('gptme:show-external-sessions', 'true');
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          externalSessions={[externalSession]}
          onSelectExternal={onSelectExternal}
        />
      );
      fireEvent.click(screen.getByText('My CC Session'));
      expect(onSelectExternal).toHaveBeenCalledWith('ext-1');
    });

    it('external sessions appear mixed inline with native conversations in the date-grouped view', () => {
      // Native conversation modified more recently than the external session
      const recentNative = createConversation({
        id: 'native-recent',
        name: 'Recent Native',
        modified: Date.now() / 1000,
      });
      // External session modified 1 hour ago
      const olderExternal = {
        ...externalSession,
        last_activity: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
      };
      localStorage.setItem('gptme:show-external-sessions', 'true');
      renderWithProviders(
        <ConversationList
          {...defaultProps}
          conversations={[recentNative]}
          externalSessions={[olderExternal]}
          onSelectExternal={onSelectExternal}
        />
      );
      // Both appear — no separate "External Sessions" header in the recent/default view
      expect(screen.getByText('Recent Native')).toBeInTheDocument();
      expect(screen.getByText('My CC Session')).toBeInTheDocument();
      expect(screen.queryByText('External Sessions')).not.toBeInTheDocument();
      // Harness badge is present
      expect(screen.getByText('CC')).toBeInTheDocument();
    });
  });
});
