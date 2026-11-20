import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import { MemoryRouter } from 'react-router-dom';
import MainLayout from '../MainLayout';
import { serverRegistry$ } from '@/stores/servers';
import { rightSidebarActiveTab$, rightSidebarVisible$ } from '@/stores/sidebar';

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ api: {}, isConnected$: observable(true) }),
}));

jest.mock('@/hooks/useConnectionStatus', () => ({
  useConnectionStatus: () => 'connected',
}));

jest.mock('@/hooks/useConversationsInfiniteQuery', () => ({
  useConversationsInfiniteQuery: () => ({
    data: {
      pages: [
        {
          conversations: [{ id: 'conv-1', name: 'Same ID chat', modified: 1, messages: 2 }],
          nextCursor: undefined,
        },
      ],
    },
    isError: false,
    error: null,
    isLoading: false,
    isFetching: false,
    fetchNextPage: jest.fn(),
    hasNextPage: false,
    refetch: jest.fn(),
  }),
}));

jest.mock('@/hooks/useMultiServerConversations', () => ({
  useSecondaryServerConversations: () => ({
    secondaryConversations: [],
    connectedServerCount: 1,
  }),
}));

jest.mock('@/stores/tasks', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable } = require('@legendapp/state');
  return {
    selectedTask$: observable(null),
    useTasksQuery: () => ({ data: [], isLoading: false, error: null, refetch: jest.fn() }),
    useTaskQuery: () => ({ data: undefined }),
    useCreateTaskMutation: () => ({ mutateAsync: jest.fn() }),
  };
});

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn(), cancelQueries: jest.fn() }),
}));

jest.mock('@/democonversations', () => ({
  DEMO_AGENT: undefined,
  demoConversations: [],
  getDemoMessages: () => [],
}));

jest.mock('@/utils/connectionConfig', () => ({
  ...jest.requireActual('@/utils/connectionConfig'),
  shouldShowDemoContent: () => false,
}));

jest.mock('@/components/ui/resizable', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require('react');
  return {
    ResizablePanelGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    ResizablePanel: React.forwardRef(
      ({ children }: { children: React.ReactNode }, ref: React.Ref<unknown>) => {
        React.useImperativeHandle(ref, () => ({
          expand: jest.fn(),
          collapse: jest.fn(),
          resize: jest.fn(),
          isCollapsed: () => false,
        }));
        return <div>{children}</div>;
      }
    ),
    ResizableHandle: () => null,
  };
});

jest.mock('@/components/ui/sheet', () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

jest.mock('@/components/ConversationContent', () => ({
  ConversationContent: () => <div data-testid="conversation-content" />,
}));
jest.mock('@/components/UnifiedSidebar', () => ({ UnifiedSidebar: () => null }));
jest.mock('@/components/SidebarIcons', () => ({ SidebarIcons: () => null }));
jest.mock('@/components/RightSidebar', () => ({ RightSidebar: () => null }));
jest.mock('@/components/SettingsModal', () => ({ SettingsModal: () => null }));
jest.mock('@/components/MobileBottomNav', () => ({ MobileBottomNav: () => null }));
jest.mock('@/components/TaskCreationDialog', () => ({ TaskCreationDialog: () => null }));
jest.mock('@/components/WelcomeView', () => ({
  WelcomeView: () => <div data-testid="new-chat-composer" />,
}));
jest.mock('@/components/ExternalSessionDetail', () => ({
  ExternalSessionDetail: () => null,
}));
jest.mock('@/components/SplitConversationView', () => ({ SplitConversationView: () => null }));
jest.mock('@/components/TaskDetails', () => ({ TaskDetails: () => null }));
jest.mock('@/components/AgentsView', () => ({ AgentsView: () => null }));
jest.mock('@/components/WorkspacesView', () => ({ WorkspacesView: () => null }));
jest.mock('../ConversationSettings', () => ({
  ConversationSettings: ({
    conversationId,
    serverId,
  }: {
    conversationId: string;
    serverId?: string;
  }) => (
    <div data-testid="settings-panel">
      {conversationId}:{serverId ?? 'primary'}
    </div>
  ),
}));

const SERVER_ID = 'server-1';
const SECONDARY_SERVER_ID = 'server-2';

function renderLayout(path: string, conversationId: string | undefined = 'conv-1') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MainLayout conversationId={conversationId || undefined} />
    </MemoryRouter>
  );
}

describe('MainLayout right sidebar owner threading', () => {
  beforeEach(() => {
    rightSidebarVisible$.set(true);
    rightSidebarActiveTab$.set('settings');
    serverRegistry$.set({
      servers: [
        {
          id: SERVER_ID,
          name: 'Local',
          baseUrl: 'http://localhost:5700',
          authToken: null,
          useAuthToken: false,
          isPreset: true,
          createdAt: 0,
          lastUsedAt: 0,
        },
        {
          id: SECONDARY_SERVER_ID,
          name: 'Secondary',
          baseUrl: 'http://localhost:5701',
          authToken: null,
          useAuthToken: false,
          isPreset: false,
          createdAt: 0,
          lastUsedAt: 0,
        },
      ],
      activeServerId: SERVER_ID,
      connectedServerIds: [SERVER_ID, SECONDARY_SERVER_ID],
    });
  });

  it.each([SERVER_ID, SECONDARY_SERVER_ID, 'unknown'])(
    'keeps deep-linked agent history off the new-chat composer on %s',
    (serverId) => {
      renderLayout(`/chat?agentPath=%2Fagents%2Fbuilder&server=${serverId}`, '');

      expect(screen.getByRole('heading', { name: 'Agent conversation history' })).toBeVisible();
      expect(screen.queryByTestId('new-chat-composer')).not.toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Back to agent overview' })).toHaveAttribute(
        'href',
        `/agents/overview?path=%2Fagents%2Fbuilder&server=${serverId}`
      );
    }
  );

  it('retains the ordinary new-chat composer without an agent history filter', () => {
    renderLayout('/chat', '');
    expect(screen.getByTestId('new-chat-composer')).toBeInTheDocument();
  });

  it('opens an existing conversation instead of the history landing page', async () => {
    renderLayout(`/chat/conv-1?agentPath=%2Fagents%2Fbuilder&server=${SECONDARY_SERVER_ID}`);
    await waitFor(() => expect(screen.getByTestId('conversation-content')).toBeInTheDocument());
    expect(screen.queryByRole('heading', { name: 'Agent conversation history' })).toBeNull();
    expect(screen.queryByTestId('new-chat-composer')).toBeNull();
  });

  it('passes the conversation owner to the right sidebar', async () => {
    renderLayout('/chat/conv-1');

    await waitFor(() =>
      expect(screen.getByTestId('settings-panel')).toHaveTextContent(`conv-1:${SERVER_ID}`)
    );
  });

  it('lets the explicit server URL parameter override the summary owner', async () => {
    renderLayout(`/chat/conv-1?server=${SECONDARY_SERVER_ID}`);

    await waitFor(() =>
      expect(screen.getByTestId('settings-panel')).toHaveTextContent(
        `conv-1:${SECONDARY_SERVER_ID}`
      )
    );
  });
});
