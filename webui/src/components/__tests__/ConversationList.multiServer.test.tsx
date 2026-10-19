import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { observable } from '@legendapp/state';
import { MemoryRouter } from 'react-router-dom';
import { ConversationList } from '../ConversationList';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { ConversationSummary } from '@/types/conversation';
import type { IApiClient } from '@/utils/api';
import { getClientForServer } from '@/stores/serverClients';
import { conversations$, selectedConversation$ } from '@/stores/conversations';
import { toast } from 'sonner';

const mockGetClientForServer = getClientForServer as jest.MockedFunction<typeof getClientForServer>;

const mockPrimaryApi = {
  baseUrl: 'http://primary.test',
  deleteConversation: jest.fn().mockResolvedValue(undefined),
  getChatConfig: jest.fn(),
  updateChatConfig: jest.fn().mockResolvedValue(undefined),
  patchConversationMetadata: jest.fn().mockResolvedValue(undefined),
};

const mockSecondaryClient = {
  baseUrl: 'http://secondary.test',
  deleteConversation: jest.fn().mockResolvedValue(undefined),
  getChatConfig: jest.fn().mockResolvedValue({
    chat: { name: 'Secondary Chat', model: 'keep-model' },
    env: { KEEP: 'me' },
    mcp: { servers: [] },
  }),
  updateChatConfig: jest.fn().mockResolvedValue(undefined),
  patchConversationMetadata: jest.fn().mockResolvedValue(undefined),
};

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: mockPrimaryApi,
    connectionConfig: { baseUrl: 'http://primary.test', apiKey: '' },
    isConnected$: observable(true),
  }),
}));

// Keep the real row component but replace Radix's focus-managed context menu
// with a minimal one so a test can edit the rename input without an auto-blur
// submitting the unchanged name first.
jest.mock('@/components/ui/context-menu', () => {
  /* eslint-disable @typescript-eslint/no-require-imports, @typescript-eslint/consistent-type-imports */
  const React = require('react') as typeof import('react');
  /* eslint-enable @typescript-eslint/no-require-imports, @typescript-eslint/consistent-type-imports */
  const OpenContext = React.createContext<{
    open: boolean;
    setOpen: (open: boolean) => void;
  }>({ open: false, setOpen: () => {} });
  const children = ({ children: c }: { children?: React.ReactNode }) => <>{c}</>;
  const Content = ({ children: c }: { children?: React.ReactNode }) => {
    const { open } = React.useContext(OpenContext);
    return open ? <div>{c}</div> : null;
  };
  return {
    ContextMenu: ({ children: c }: { children?: React.ReactNode }) => {
      const [open, setOpen] = React.useState(false);
      return (
        <OpenContext.Provider value={{ open, setOpen }}>
          <div onContextMenu={() => setOpen(true)}>{c}</div>
        </OpenContext.Provider>
      );
    },
    ContextMenuTrigger: children,
    ContextMenuContent: Content,
    ContextMenuItem: ({
      children: c,
      onClick,
    }: {
      children?: React.ReactNode;
      onClick?: (event: React.MouseEvent) => void;
    }) => <button onClick={onClick}>{c}</button>,
    ContextMenuSeparator: () => <hr />,
    ContextMenuSub: children,
    ContextMenuSubContent: children,
    ContextMenuSubTrigger: children,
  };
});

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: jest.fn((serverId: string) => {
    if (serverId === 'secondary') return mockSecondaryClient;
    if (serverId === 'primary') return mockPrimaryApi;
    return null;
  }),
}));

jest.mock('@/stores/servers', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable: obs } = require('@legendapp/state');
  return {
    serverRegistry$: obs({
      servers: [
        { id: 'primary', name: 'Primary', baseUrl: 'http://primary.test' },
        { id: 'secondary', name: 'Secondary', baseUrl: 'http://secondary.test' },
      ],
      activeServerId: 'primary',
      connectedServerIds: ['primary', 'secondary'],
    }),
  };
});

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

const mockInvalidateQueries = jest.fn();
jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

jest.mock('@/democonversations', () => ({
  demoConversations: [],
  getDemoMessages: () => [],
}));

jest.mock('@/stores/conversations', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { observable: obs } = require('@legendapp/state');
  return {
    conversations$: obs(new Map()),
    selectedConversation$: obs(''),
  };
});

const sharedId = 'shared-conversation';

const primaryConversation: ConversationSummary = {
  id: sharedId,
  serverId: 'primary',
  serverName: 'Primary',
  name: 'Primary Chat',
  messages: 1,
  modified: Date.now() / 1000,
  readonly: false,
};

const secondaryConversation: ConversationSummary = {
  ...primaryConversation,
  serverId: 'secondary',
  serverName: 'Secondary',
  name: 'Secondary Chat',
};

const renderList = (
  conversations: ConversationSummary[] = [primaryConversation, secondaryConversation]
) =>
  render(
    <MemoryRouter initialEntries={['/chat/shared-conversation']}>
      <TooltipProvider>
        <ConversationList
          conversations={conversations}
          onSelect={jest.fn()}
          fetchNextPage={jest.fn()}
        />
      </TooltipProvider>
    </MemoryRouter>
  );

function secondaryRow(): HTMLElement {
  return screen.getByText('Secondary Chat').closest('[role="button"]') as HTMLElement;
}

describe('ConversationList sidebar mutation ownership (two servers, same id)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetClientForServer.mockImplementation((serverId: string) => {
      if (serverId === 'secondary') return mockSecondaryClient as unknown as IApiClient;
      if (serverId === 'primary') return mockPrimaryApi as unknown as IApiClient;
      return null;
    });
    conversations$.set(new Map());
    selectedConversation$.set(sharedId);
  });

  it('stars the secondary row through its owning client, not the primary', async () => {
    renderList();
    fireEvent.click(within(secondaryRow()).getByLabelText('Star conversation'));

    await waitFor(() =>
      expect(mockSecondaryClient.patchConversationMetadata).toHaveBeenCalledWith(sharedId, {
        starred: true,
      })
    );
    expect(mockPrimaryApi.patchConversationMetadata).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(mockInvalidateQueries).toHaveBeenCalledWith({
        queryKey: ['secondary-conversations', 'secondary'],
      })
    );
  });

  it('renames the edited secondary row through its owning client, preserving other config', async () => {
    renderList();
    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(screen.getByText('Rename'));

    // The identical primary row must not enter rename mode.
    expect(screen.getAllByTestId('conversation-rename-input')).toHaveLength(1);
    const input = screen.getByTestId('conversation-rename-input') as HTMLInputElement;
    // Edit before the context menu's auto-focus teardown can blur the input.
    fireEvent.change(input, { target: { value: 'Renamed Secondary' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(mockSecondaryClient.getChatConfig).toHaveBeenCalledWith(sharedId));
    expect(mockSecondaryClient.updateChatConfig).toHaveBeenCalledWith(sharedId, {
      chat: { name: 'Renamed Secondary', model: 'keep-model' },
      env: { KEEP: 'me' },
      mcp: { servers: [] },
    });
    expect(mockPrimaryApi.getChatConfig).not.toHaveBeenCalled();
    expect(mockPrimaryApi.updateChatConfig).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(mockInvalidateQueries).toHaveBeenCalledWith({
        queryKey: ['secondary-conversations', 'secondary'],
      })
    );
  });

  it('deletes through the owning client without clearing unrelated primary selection or cache', async () => {
    (conversations$.set as (value: Map<string, unknown>) => void)(
      new Map([[sharedId, { data: { name: 'Primary Chat' } }]])
    );
    const primaryCacheBefore = conversations$.get(sharedId)?.peek();
    render(
      <MemoryRouter initialEntries={['/chat/shared-conversation']}>
        <TooltipProvider>
          <ConversationList
            conversations={[primaryConversation, secondaryConversation]}
            onSelect={jest.fn()}
            fetchNextPage={jest.fn()}
            showServerLabels
          />
        </TooltipProvider>
      </MemoryRouter>
    );

    // The global cache holds a Primary Chat title. The secondary row must keep
    // its own summary title instead of borrowing the primary's cached name.
    expect(screen.getByText('Primary Chat')).toBeInTheDocument();
    expect(screen.getByText('Secondary Chat')).toBeInTheDocument();
    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(mockSecondaryClient.deleteConversation).toHaveBeenCalledWith(sharedId)
    );
    expect(mockPrimaryApi.deleteConversation).not.toHaveBeenCalled();
    // URL has no ?server, so the selected conversation belongs to the primary
    // server with the same id. Deleting the secondary row must not clear it.
    expect(selectedConversation$.get()).toBe(sharedId);
    expect(conversations$.get(sharedId)?.peek()).toEqual(primaryCacheBefore);
    await waitFor(() =>
      expect(mockInvalidateQueries).toHaveBeenCalledWith({
        queryKey: ['secondary-conversations', 'secondary'],
      })
    );
    expect(mockInvalidateQueries).not.toHaveBeenCalledWith({
      queryKey: ['conversations', 'http://primary.test'],
    });
  });

  it('removes the loaded store entry when deleting the server that owns it', async () => {
    // The secondary conversation was opened earlier, then the user selected a
    // different conversation before deleting it from the sidebar.
    selectedConversation$.set('another-chat');
    (conversations$.set as (value: Map<string, unknown>) => void)(
      new Map([[sharedId, { serverId: 'secondary', data: { name: 'Secondary Chat', log: [] } }]])
    );
    renderList([secondaryConversation]);

    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(mockSecondaryClient.deleteConversation).toHaveBeenCalledWith(sharedId)
    );
    // Without this the refreshed secondary query drops the row and MainLayout
    // re-adds the stale loaded entry as an ownerless conversation.
    await waitFor(() => expect(conversations$.get(sharedId)?.peek()).toBeUndefined());
  });

  it('shows live indicators on a secondary row that owns the loaded entry', () => {
    (conversations$.set as (value: Map<string, unknown>) => void)(
      new Map([
        [
          sharedId,
          {
            serverId: 'secondary',
            isGenerating: true,
            isConnected: false,
            data: { name: 'Secondary Chat', log: [] },
          },
        ],
      ])
    );
    renderList([secondaryConversation]);

    expect(secondaryRow().querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('does not show another server’s live state on a same-id row', () => {
    (conversations$.set as (value: Map<string, unknown>) => void)(
      new Map([
        [
          sharedId,
          {
            serverId: 'primary',
            isGenerating: true,
            isConnected: false,
            data: { name: 'Primary Chat', log: [] },
          },
        ],
      ])
    );
    renderList();

    expect(secondaryRow().querySelector('.animate-spin')).not.toBeInTheDocument();
  });

  it('treats an explicit registered primary serverId as primary, not secondary', async () => {
    renderList([primaryConversation]);
    fireEvent.click(screen.getByLabelText('Star conversation'));

    await waitFor(() => expect(mockPrimaryApi.patchConversationMetadata).toHaveBeenCalled());
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ['conversations', 'http://primary.test'],
    });
    expect(mockInvalidateQueries).not.toHaveBeenCalledWith({
      queryKey: ['secondary-conversations', 'primary'],
    });
  });

  it('renames a secondary row while it is the active server through the secondary family only', async () => {
    const { serverRegistry$ } = await import('@/stores/servers');
    // Activate the secondary server; primary-ness must follow client
    // ownership, not active-server identity.
    serverRegistry$.set({
      ...serverRegistry$.get(),
      activeServerId: 'secondary',
    });
    // The store entry belongs to the primary server (same bare ID).
    (conversations$.set as (value: Map<string, unknown>) => void)(
      new Map([[sharedId, { serverId: 'primary', data: { name: 'Primary Chat' } }]])
    );

    renderList();
    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(screen.getByText('Rename'));
    const input = screen.getByTestId('conversation-rename-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Renamed Secondary' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(mockSecondaryClient.updateChatConfig).toHaveBeenCalled());
    // The secondary query family is invalidated, never the primary list.
    await waitFor(() =>
      expect(mockInvalidateQueries).toHaveBeenCalledWith({
        queryKey: ['secondary-conversations', 'secondary'],
      })
    );
    expect(mockInvalidateQueries).not.toHaveBeenCalledWith({
      queryKey: ['conversations', 'http://primary.test'],
    });
    // The primary-owned store entry must not be clobbered.
    expect(conversations$.get(sharedId)?.peek()?.data.name).toBe('Primary Chat');

    // Restore the default registry for other tests.
    serverRegistry$.set({ ...serverRegistry$.get(), activeServerId: 'primary' });
  });

  it('reverts optimistic star state on failure without touching the primary', async () => {
    mockSecondaryClient.patchConversationMetadata.mockRejectedValueOnce(new Error('nope'));
    renderList();

    fireEvent.click(within(secondaryRow()).getByLabelText('Star conversation'));

    await waitFor(() =>
      expect(mockSecondaryClient.patchConversationMetadata).toHaveBeenCalledWith(sharedId, {
        starred: true,
      })
    );
    await waitFor(() =>
      expect(within(secondaryRow()).getByLabelText('Star conversation')).toBeInTheDocument()
    );
    expect(mockPrimaryApi.patchConversationMetadata).not.toHaveBeenCalled();
    expect(mockInvalidateQueries).not.toHaveBeenCalledWith({
      queryKey: ['secondary-conversations', 'secondary'],
    });
  });

  it('does not report rename success when the owning-client update fails', async () => {
    mockSecondaryClient.updateChatConfig.mockRejectedValueOnce(new Error('nope'));
    renderList();
    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(screen.getByText('Rename'));

    const input = screen.getByTestId('conversation-rename-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Renamed Secondary' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to rename conversation'));
    expect(toast.success).not.toHaveBeenCalledWith('Conversation renamed');
    expect(mockPrimaryApi.getChatConfig).not.toHaveBeenCalled();
    expect(mockPrimaryApi.updateChatConfig).not.toHaveBeenCalled();
    expect(screen.queryByTestId('conversation-rename-input')).not.toBeInTheDocument();
  });

  it('fails closed for star and rename if the owner disappears after render', async () => {
    renderList();
    mockGetClientForServer.mockReturnValue(null);

    fireEvent.click(within(secondaryRow()).getByLabelText('Star conversation'));
    await waitFor(() => expect(mockGetClientForServer).toHaveBeenCalledWith('secondary'));
    expect(mockSecondaryClient.patchConversationMetadata).not.toHaveBeenCalled();
    expect(mockPrimaryApi.patchConversationMetadata).not.toHaveBeenCalled();

    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(screen.getByText('Rename'));
    const input = screen.getByTestId('conversation-rename-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Renamed Secondary' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Failed to rename conversation: owning server is no longer available'
      )
    );
    expect(mockSecondaryClient.getChatConfig).not.toHaveBeenCalled();
    expect(mockPrimaryApi.getChatConfig).not.toHaveBeenCalled();
  });

  it('fails closed when the owning server disappears after the delete dialog opens', async () => {
    renderList();
    fireEvent.contextMenu(screen.getByText('Secondary Chat'));
    fireEvent.click(screen.getByText('Delete'));

    // Owner was registered when the menu opened; it disappears before confirm.
    mockGetClientForServer.mockReturnValue(null);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(
        screen.getByText(/owning server.*no longer (available|connected)/i)
      ).toBeInTheDocument()
    );
    expect(mockPrimaryApi.deleteConversation).not.toHaveBeenCalled();
    expect(mockSecondaryClient.deleteConversation).not.toHaveBeenCalled();
  });
});
