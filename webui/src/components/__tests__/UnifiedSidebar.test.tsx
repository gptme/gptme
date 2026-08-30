import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UnifiedSidebar } from '../UnifiedSidebar';

const mockIsDemoMode = jest.fn(() => false);

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

jest.mock('@/contexts/ApiContext', () => {
  const { observable: createObservable } = jest.requireActual('@legendapp/state');
  const isConnected$ = createObservable(true);
  return {
    useApi: () => ({
      api: { getExternalSessions: jest.fn() },
      connectionConfig: { baseUrl: 'demo://offline' },
      isConnected$,
    }),
  };
});

const selectedConversationId$ = observable<string | null>(null);

const renderTaskSidebar = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/tasks']}>
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
