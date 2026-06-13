import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HistoryView } from '../HistoryView';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockNavigate = jest.fn();
const mockUseQuery = jest.fn();
const mockIntersectionObserver = jest.fn(() => ({
  observe: jest.fn(),
  disconnect: jest.fn(),
  unobserve: jest.fn(),
}));
const baseConversation = {
  id: 'chat-1',
  name: 'Accessible chat',
  modified: Date.UTC(2026, 4, 31, 12, 0, 0) / 1000,
  messages: 3,
  last_message_preview: 'Latest preview',
  last_message_role: 'user',
  workspace: '.',
};

const setUseQueryResults = ({
  allConversations = [baseConversation],
  serverSearchResults = undefined,
  isSearchFetching = false,
}: {
  allConversations?: (typeof baseConversation)[];
  serverSearchResults?: (typeof baseConversation)[] | undefined;
  isSearchFetching?: boolean;
}) => {
  mockUseQuery.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => {
    switch (queryKey[0]) {
      case 'conversations-all':
        return {
          data: allConversations,
          isLoading: false,
        };
      case 'conversations-search':
        return {
          data: serverSearchResults,
          isFetching: isSearchFetching,
        };
      default:
        throw new Error(`Unexpected query key: ${String(queryKey[0])}`);
    }
  });
};

jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

jest.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => mockUseQuery(...args),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: jest.fn(() => true),
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      isConnected$: {},
      getConversations: jest.fn(),
      searchConversations: jest.fn(),
    },
    connectionConfig: { baseUrl: 'http://localhost:5700' },
  }),
}));

jest.mock('@/components/ActivityCalendar', () => ({
  ActivityCalendar: () => <div data-testid="activity-calendar" />,
}));

describe('HistoryView', () => {
  const renderHistoryView = () =>
    render(
      <TooltipProvider>
        <HistoryView />
      </TooltipProvider>
    );

  beforeAll(() => {
    Object.defineProperty(window, 'IntersectionObserver', {
      writable: true,
      configurable: true,
      value: mockIntersectionObserver,
    });
    Object.defineProperty(global, 'IntersectionObserver', {
      writable: true,
      configurable: true,
      value: mockIntersectionObserver,
    });
  });

  beforeEach(() => {
    mockNavigate.mockReset();
    mockUseQuery.mockReset();
    mockIntersectionObserver.mockClear();
    setUseQueryResults({});
  });

  it('labels history controls and exposes conversation rows as buttons', () => {
    renderHistoryView();

    expect(screen.getByRole('button', { name: 'Back to chat' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous year' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next year' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Search conversations' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Accessible chat/i })).toBeInTheDocument();
  });

  it('navigates when a conversation row button is clicked', async () => {
    const user = userEvent.setup();
    renderHistoryView();

    await user.click(screen.getByRole('button', { name: /Accessible chat/i }));

    expect(mockNavigate).toHaveBeenCalledWith('/chat/chat-1');
  });

  it('shows a notice when server-side search may be capped at 200 results', () => {
    jest.useFakeTimers();
    setUseQueryResults({
      serverSearchResults: Array.from({ length: 200 }, (_, index) => ({
        ...baseConversation,
        id: `server-chat-${index}`,
        name: `Server chat ${index}`,
      })),
    });

    renderHistoryView();

    fireEvent.change(screen.getByRole('textbox', { name: 'Search conversations' }), {
      target: { value: 'server' },
    });

    act(() => {
      jest.advanceTimersByTime(300);
    });

    expect(
      screen.getByText(
        'Showing up to 200 server search matches. Refine your query if expected conversations are missing.'
      )
    ).toBeInTheDocument();

    jest.useRealTimers();
  });
});
