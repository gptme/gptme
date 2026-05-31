import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { HistoryView } from '../HistoryView';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockNavigate = jest.fn();
const mockUseQuery = jest.fn();

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
      getConversationsPaginated: jest.fn(),
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

  beforeEach(() => {
    mockNavigate.mockReset();
    mockUseQuery.mockReset();
    mockUseQuery.mockReturnValue({
      data: [
        {
          id: 'chat-1',
          name: 'Accessible chat',
          modified: Date.UTC(2026, 4, 31, 12, 0, 0) / 1000,
          messages: 3,
          last_message_preview: 'Latest preview',
          last_message_role: 'user',
          workspace: '.',
        },
      ],
      isLoading: false,
    });
  });

  it('labels history controls and exposes conversation rows as buttons', () => {
    renderHistoryView();

    expect(screen.getByRole('button', { name: 'Back to chat' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous year' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next year' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Search conversations' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Accessible chat/i })).toBeInTheDocument();
  });

  it('navigates when a conversation row button is clicked', () => {
    renderHistoryView();

    fireEvent.click(screen.getByRole('button', { name: /Accessible chat/i }));

    expect(mockNavigate).toHaveBeenCalledWith('/chat/chat-1');
  });
});
