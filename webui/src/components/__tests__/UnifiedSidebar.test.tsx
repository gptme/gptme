import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UnifiedSidebar } from '../UnifiedSidebar';
import { observable } from '@legendapp/state';
import type { Task } from '@/types/task';

const mockIsDemoMode = jest.fn(() => false);

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

jest.mock('@/contexts/ApiContext', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    useApi: () => ({
      api: { importConversation: jest.fn() },
      connectionConfig: { baseUrl: 'demo://offline' },
      isConnected$: observable(false),
    }),
  };
});

jest.mock('@/stores/sidebar', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    selectedWorkspace$: observable(''),
    selectedAgent$: observable(''),
    leftSidebarVisible$: observable(true),
  };
});

jest.mock('@/stores/conversations', () => ({
  initConversation: jest.fn(),
}));

jest.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: undefined, isLoading: false }),
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

const baseProps = {
  conversations: [],
  selectedConversationId$: observable(''),
  onSelectConversation: jest.fn(),
  fetchNextPage: jest.fn(),
  tasks: [] as Task[],
  onSelectTask: jest.fn(),
  onCreateTask: jest.fn(),
};

const renderSidebar = (props = baseProps) =>
  render(
    <MemoryRouter initialEntries={['/tasks']}>
      <UnifiedSidebar {...props} />
    </MemoryRouter>
  );

describe('UnifiedSidebar — demo mode task creation gate', () => {
  beforeEach(() => {
    mockIsDemoMode.mockReturnValue(false);
  });

  it('shows Create Task button when connected to a live server', () => {
    renderSidebar();
    expect(screen.getByRole('button', { name: 'Create task' })).toBeInTheDocument();
  });

  it('hides Create Task button in offline demo mode', () => {
    mockIsDemoMode.mockReturnValue(true);
    renderSidebar();
    expect(screen.queryByRole('button', { name: 'Create task' })).not.toBeInTheDocument();
  });

  it('shows an explanation in offline demo mode instead of "No tasks yet"', () => {
    mockIsDemoMode.mockReturnValue(true);
    renderSidebar();
    expect(screen.getByText('Task creation requires a live gptme server.')).toBeInTheDocument();
    expect(screen.queryByText('No tasks yet')).not.toBeInTheDocument();
  });
});
