import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AdminView } from '../AdminView';
import type { ActiveSession } from '@/types/api';

let mockIsConnected = true;
const mockGetSessions = jest.fn();
const mockDeleteSession = jest.fn();

jest.mock('@legendapp/state/react', () => ({
  use$: () => mockIsConnected,
}));

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      isConnected$: {},
      getSessions: mockGetSessions,
      deleteSession: mockDeleteSession,
    },
  }),
}));

const session: ActiveSession = {
  id: 'session-12345678',
  conversation_id: 'cached-conversation',
  model: 'test-model',
  message_count: 3,
  generating: false,
  elapsed_seconds: null,
  created_at: '2026-11-17T12:00:00Z',
  last_activity: '2026-11-17T12:00:00Z',
};

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = () => (
    <QueryClientProvider client={client}>
      <AdminView />
    </QueryClientProvider>
  );
  return { client, view };
}

const prompt = 'Connect to a gptme server to view sessions.';

describe('AdminView connection state', () => {
  beforeEach(() => {
    mockIsConnected = true;
    mockGetSessions.mockReset().mockResolvedValue([session]);
    mockDeleteSession.mockReset();
  });

  it('hides cached rows and disables refresh after a connection drops', async () => {
    const { client, view } = setup();
    const { rerender } = render(view());
    expect(await screen.findByText('cached-conversation')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Kill session/ })).toBeInTheDocument();

    mockIsConnected = false;
    rerender(view());

    expect(screen.getByText(prompt)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Kill session/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled();
    expect(screen.getAllByText('—')).toHaveLength(3);
    // Exercise the real disabled-query cache behavior, rather than clearing the data.
    expect(client.getQueryData(['admin-sessions'])).toEqual([session]);

    mockIsConnected = true;
    rerender(view());
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    client.clear();
  });

  it('replaces a previous query error with the connect prompt after disconnect', async () => {
    mockGetSessions.mockRejectedValue(new Error('Server unavailable'));
    const { client, view } = setup();
    const { rerender } = render(view());
    expect(await screen.findByText('Failed to load sessions')).toBeInTheDocument();

    mockIsConnected = false;
    rerender(view());

    expect(screen.getByText(prompt)).toBeInTheDocument();
    expect(screen.queryByText('Failed to load sessions')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    client.clear();
  });

  it('keeps the empty-state message when connected with no active sessions', async () => {
    mockGetSessions.mockResolvedValue([]);
    const { client, view } = setup();
    render(view());

    expect(await screen.findByText('No active sessions')).toBeInTheDocument();
    expect(screen.queryByText(prompt)).not.toBeInTheDocument();
    client.clear();
  });
});
