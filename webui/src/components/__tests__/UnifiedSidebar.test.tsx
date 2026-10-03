import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UnifiedSidebar } from '../UnifiedSidebar';

const mockIsDemoMode = jest.fn(() => false);

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

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

const renderTaskSidebar = (initialEntry = '/tasks') => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
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
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('UnifiedSidebar task creation', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
  });

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
    renderTaskSidebar('/chat');

    await waitFor(() => expect(mockGetServerInfo).toHaveBeenCalledTimes(1));
    expect(mockGetExternalSessions).not.toHaveBeenCalled();
  });

  it('does not request the catalog when the server reports no capabilities', async () => {
    mockGetServerInfo.mockResolvedValue({ version: '0.29.0' });
    renderTaskSidebar('/chat');

    await waitFor(() => expect(mockGetServerInfo).toHaveBeenCalledTimes(1));
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
