import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationList } from '../ConversationList';
import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import type { ConversationSummary } from '@/types/conversation';
import { TooltipProvider } from '@/components/ui/tooltip';

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
const renderWithProviders = (ui: React.ReactElement) => {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
};

describe('ConversationList', () => {
  const defaultProps = {
    conversations: [createConversation()],
    onSelect: jest.fn(),
    fetchNextPage: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
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

  // Note: Radix UI ContextMenu requires pointer events that JSDOM doesn't fully support.
  // Context menu functionality (rename, delete, export) is tested via the ConversationSettings
  // component tests and manual testing. The context menu wraps existing functionality that
  // is already tested elsewhere (DeleteConversationConfirmationDialog, exportConversation utils).
});
