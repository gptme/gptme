import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { DeleteConversationConfirmationDialog } from '../DeleteConversationConfirmationDialog';
import { serverRegistry$ } from '@/stores/servers';
import {
  conversations$ as mockConversations$,
  selectedConversation$ as mockSelectedConversation$,
} from '@/stores/conversations';

const mockDeleteConversation = jest.fn().mockResolvedValue(undefined);
const mockSecondaryDeleteConversation = jest.fn().mockResolvedValue(undefined);
const mockGetClientForServer = jest.fn();
const testConversations$ = mockConversations$ as unknown as {
  set: (value: Map<string, { data: object }>) => void;
};

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: (serverId: string) => mockGetClientForServer(serverId),
}));

jest.mock('@/stores/servers', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable: obs } = require('@legendapp/state');
  return {
    serverRegistry$: obs({
      servers: [{ id: 'primary', name: 'Primary', baseUrl: 'http://localhost:5700' }],
      activeServerId: 'primary',
      connectedServerIds: ['primary'],
    }),
  };
});

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => {
    // Production's primary client follows activeServerId, not a fixed server.
    const { serverRegistry$ } = jest.requireMock('@/stores/servers');
    return {
      api: {
        deleteConversation:
          serverRegistry$.peek().activeServerId === 'secondary'
            ? mockSecondaryDeleteConversation
            : mockDeleteConversation,
      },
      connectionConfig: { baseUrl: 'http://localhost:5700' },
    };
  },
}));

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}));

jest.mock('@/stores/conversations', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable } = require('@legendapp/state');
  return {
    conversations$: observable(new Map()),
    selectedConversation$: observable(''),
  };
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderDialog(
  conversationName = 'selected-chat',
  currentRoute = `/chat/${conversationName}`,
  serverId?: string
) {
  const onDelete = jest.fn();
  render(
    <MemoryRouter initialEntries={[currentRoute]}>
      <LocationProbe />
      <DeleteConversationConfirmationDialog
        conversationName={conversationName}
        serverId={serverId}
        open={true}
        onOpenChange={jest.fn()}
        onDelete={onDelete}
      />
    </MemoryRouter>
  );
  return { onDelete };
}

describe('DeleteConversationConfirmationDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    serverRegistry$.activeServerId.set('primary');
    mockGetClientForServer.mockReturnValue({
      baseUrl: 'http://secondary.test',
      deleteConversation: mockSecondaryDeleteConversation,
    });
    testConversations$.set(
      new Map([
        ['selected-chat', { data: {} }],
        ['other-chat', { data: {} }],
      ])
    );
    mockSelectedConversation$.set('selected-chat');
  });

  it('redirects to the chat home after deleting the selected conversation', async () => {
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat$/));
    expect(mockDeleteConversation).toHaveBeenCalledWith('selected-chat');
    expect(mockSelectedConversation$.get()).toBe('');
  });

  it('keeps the current route and selection when deleting another conversation', async () => {
    const { onDelete } = renderDialog('other-chat', '/chat/selected-chat');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat\/selected-chat$/);
    expect(mockSelectedConversation$.get()).toBe('selected-chat');
  });

  it('uses the selected ?server owner and redirects when deleting that server row', async () => {
    renderDialog('selected-chat', '/chat/selected-chat?server=secondary', 'secondary');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(mockSecondaryDeleteConversation).toHaveBeenCalledWith('selected-chat')
    );
    expect(mockDeleteConversation).not.toHaveBeenCalled();
    // The redirect happens after the delete promise resolves; wait for it
    // instead of racing the continuation under parallel suite load.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat$/));
    expect(mockSelectedConversation$.get()).toBe('');
  });

  it('does not clear a same-id selection that switched server during delete', async () => {
    let resolveDelete!: () => void;
    mockSecondaryDeleteConversation.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        resolveDelete = resolve;
      })
    );
    const onDelete = jest.fn();

    function SwitchServerProbe() {
      const navigate = useNavigate();
      return (
        <button onClick={() => navigate('/chat/selected-chat?server=other')}>switch-server</button>
      );
    }

    render(
      <MemoryRouter initialEntries={['/chat/selected-chat?server=secondary']}>
        <SwitchServerProbe />
        <LocationProbe />
        <DeleteConversationConfirmationDialog
          conversationName="selected-chat"
          serverId="secondary"
          open={true}
          onOpenChange={jest.fn()}
          onDelete={onDelete}
        />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() =>
      expect(mockSecondaryDeleteConversation).toHaveBeenCalledWith('selected-chat')
    );

    // The user moves selection to another server with the same ID while the
    // secondary delete is still in flight.
    fireEvent.click(screen.getByText('switch-server'));
    resolveDelete();

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat\/selected-chat$/);
    expect(mockSelectedConversation$.get()).toBe('selected-chat');
  });

  it('keeps the secondary cache when selection switches to a same-id secondary owner', async () => {
    let resolveDelete!: () => void;
    mockDeleteConversation.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        resolveDelete = resolve;
      })
    );
    const onDelete = jest.fn();

    function SwitchServerProbe() {
      const navigate = useNavigate();
      return (
        <button onClick={() => navigate('/chat/selected-chat?server=secondary')}>
          switch-server
        </button>
      );
    }

    render(
      <MemoryRouter initialEntries={['/chat/selected-chat']}>
        <SwitchServerProbe />
        <LocationProbe />
        <DeleteConversationConfirmationDialog
          conversationName="selected-chat"
          open={true}
          onOpenChange={jest.fn()}
          onDelete={onDelete}
        />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(mockDeleteConversation).toHaveBeenCalledWith('selected-chat'));

    fireEvent.click(screen.getByText('switch-server'));
    // Simulate the selected secondary conversation replacing the shared cache.
    // The deleted primary owner's payload should not be the value we preserve.
    const secondaryEntry = { serverId: 'secondary', data: { name: 'Secondary Chat' } };
    testConversations$.set(new Map([['selected-chat', secondaryEntry]]));
    resolveDelete();

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    // Check the actual stored value, not Legend State's always-truthy node.
    expect(mockConversations$.get('selected-chat')?.peek()).toEqual(secondaryEntry);
    expect(mockSelectedConversation$.get()).toBe('selected-chat');
  });

  it('removes the deleted primary owner cache even if the active server changes in flight', async () => {
    let resolveDelete!: () => void;
    mockDeleteConversation.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        resolveDelete = resolve;
      })
    );
    (mockConversations$.set as (value: Map<string, unknown>) => void)(
      new Map([['selected-chat', { serverId: 'primary', data: {} }]])
    );
    mockSelectedConversation$.set('another-chat');
    const { onDelete } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    serverRegistry$.activeServerId.set('secondary');
    resolveDelete();

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(mockDeleteConversation).toHaveBeenCalledWith('selected-chat');
    expect(mockConversations$.get('selected-chat')?.peek()).toBeUndefined();
    expect(mockSelectedConversation$.get()).toBe('another-chat');
  });

  it('treats an ownerless row as active-server owned when the secondary server is active', async () => {
    serverRegistry$.activeServerId.set('secondary');
    testConversations$.set(
      new Map([['selected-chat', { serverId: 'secondary', data: { name: 'Secondary Chat' } }]])
    );
    const { onDelete } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(mockSecondaryDeleteConversation).toHaveBeenCalledWith('selected-chat');
    expect(mockDeleteConversation).not.toHaveBeenCalled();
    expect(mockConversations$.get('selected-chat')?.peek()).toBeUndefined();
    expect(mockSelectedConversation$.get()).toBe('');
    // Navigate is called synchronously before onDelete, but the DOM location
    // update may settle after the first React flush under CI load — use waitFor.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/chat$/));
  });

  it('fails closed when the sidebar row server is no longer registered', async () => {
    mockGetClientForServer.mockReturnValue(null);
    const { onDelete } = renderDialog('selected-chat', '/chat/selected-chat', 'secondary');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(screen.getByText(/owning server is no longer available/i)).toBeInTheDocument()
    );
    expect(mockDeleteConversation).not.toHaveBeenCalled();
    expect(mockSecondaryDeleteConversation).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });
});
