import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import { CommandPalette } from '../CommandPalette';
import { conversations$, selectedConversation$, updateConversation } from '@/stores/conversations';
import { copyConversationToClipboard } from '@/utils/exportConversation';
import { toast } from 'sonner';

const mockApi = {
  searchConversations: jest.fn().mockResolvedValue([]),
  getConversation: jest.fn(),
};
const mockGetClient = jest.fn();
const mockGetClientForServer = jest.fn();
const mockIsDemoMode = jest.fn(() => false);

// Mock ApiContext with a stable `api` reference to avoid infinite re-render loop.
// useEffect in CommandPalette has [search, api] as deps — if useApi() returns a
// new object on every render, api identity changes every render → effect fires
// every render → setIsSearching(true) triggers re-render → infinite loop / OOM.
jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ api: mockApi, getClient: mockGetClient }),
}));

jest.mock('@/utils/exportConversation', () => {
  const actual = jest.requireActual('@/utils/exportConversation');
  return { ...actual, copyConversationToClipboard: jest.fn().mockResolvedValue(undefined) };
});

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: (serverId: string) => mockGetClientForServer(serverId),
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

// Mock commandPalette$ store
jest.mock('@/stores/commandPalette', () => ({
  commandPaletteOpen$: {
    get: jest.fn(() => false),
    set: jest.fn(),
    onChange: jest.fn(() => () => {}),
  },
}));

// Mock UI command components
jest.mock('../ui/command', () => ({
  CommandDialog: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
    open ? <div data-testid="command-dialog">{children}</div> : null,
  CommandInput: ({
    placeholder,
    value,
    onValueChange,
  }: {
    placeholder?: string;
    value?: string;
    onValueChange?: (value: string) => void;
  }) => (
    <input
      data-testid="command-input"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onValueChange?.(e.target.value)}
    />
  ),
  CommandList: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="command-list">{children}</div>
  ),
  CommandEmpty: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="command-empty">{children}</div>
  ),
  CommandGroup: ({ children, heading }: { children: React.ReactNode; heading?: string }) => (
    <div data-testid="command-group">
      {heading && <div data-testid="command-group-heading">{heading}</div>}
      {children}
    </div>
  ),
  CommandItem: ({
    children,
    onSelect,
    value,
  }: {
    children: React.ReactNode;
    onSelect?: () => void;
    value?: string;
  }) => (
    <div data-testid="command-item" data-value={value} onClick={() => onSelect?.()}>
      {children}
    </div>
  ),
  CommandSeparator: () => <div data-testid="command-separator" />,
}));

// Mock useNavigate
const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('CommandPalette', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    conversations$.set(new Map());
    selectedConversation$.set('');
    mockApi.getConversation.mockReset();
    mockGetClient.mockReset();
    mockGetClientForServer.mockReset();
    mockIsDemoMode.mockReset().mockReturnValue(false);
    (copyConversationToClipboard as jest.Mock).mockClear();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  const renderCommandPalette = () => {
    return render(
      <BrowserRouter>
        <CommandPalette />
      </BrowserRouter>
    );
  };

  const selectCopyCommand = async (label: string) => {
    fireEvent.keyDown(document, { key: 'k', metaKey: true });
    fireEvent.click(await screen.findByText(label));
  };

  describe('Keyboard Shortcuts', () => {
    it('opens with Cmd+K on Mac', async () => {
      renderCommandPalette();

      // Initially closed
      expect(screen.queryByPlaceholderText(/type a command/i)).not.toBeInTheDocument();

      // Press Cmd+K
      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      // Should open
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/type a command/i)).toBeInTheDocument();
      });
    });

    it('opens with Ctrl+K on Windows/Linux', async () => {
      renderCommandPalette();

      // Press Ctrl+K
      fireEvent.keyDown(document, { key: 'k', ctrlKey: true });

      // Should open
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/type a command/i)).toBeInTheDocument();
      });
    });

    it('toggles on repeated Cmd+K', async () => {
      renderCommandPalette();

      // Open
      fireEvent.keyDown(document, { key: 'k', metaKey: true });
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/type a command/i)).toBeInTheDocument();
      });

      // Close
      fireEvent.keyDown(document, { key: 'k', metaKey: true });
      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/type a command/i)).not.toBeInTheDocument();
      });
    });

    it('prevents default browser behavior', () => {
      renderCommandPalette();

      const event = new KeyboardEvent('keydown', {
        key: 'k',
        metaKey: true,
        cancelable: true,
      });
      const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

      document.dispatchEvent(event);

      expect(preventDefaultSpy).toHaveBeenCalled();
    });

    it('navigates to home with Alt+N', () => {
      renderCommandPalette();
      fireEvent.keyDown(document, { key: 'n', code: 'KeyN', altKey: true });
      expect(mockNavigate).toHaveBeenCalledWith('/');
    });

    it('prevents default browser behavior for Alt+N', () => {
      renderCommandPalette();
      const event = new KeyboardEvent('keydown', {
        key: 'n',
        code: 'KeyN',
        altKey: true,
        cancelable: true,
      });
      const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

      document.dispatchEvent(event);

      expect(preventDefaultSpy).toHaveBeenCalled();
    });

    it('does not navigate with Alt+N when typing in an input', () => {
      render(
        <BrowserRouter>
          <>
            <input data-testid="text-field" />
            <CommandPalette />
          </>
        </BrowserRouter>
      );
      const input = screen.getByTestId('text-field');
      fireEvent.keyDown(input, { key: 'n', code: 'KeyN', altKey: true });
      expect(mockNavigate).not.toHaveBeenCalled();
    });
  });

  describe('Search Functionality', () => {
    it('shows all actions when search is empty', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      await waitFor(() => {
        expect(screen.getByText('New Conversation')).toBeInTheDocument();
        expect(screen.getByText('Settings')).toBeInTheDocument();
        expect(screen.getByText('Create Agent')).toBeInTheDocument();
      });
    });

    it('filters actions by label', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'settings' } });

      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
        expect(screen.queryByText('New Conversation')).not.toBeInTheDocument();
      });
    });

    it('filters actions by description', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'start a new chat' } });

      await waitFor(() => {
        expect(screen.getByText('New Conversation')).toBeInTheDocument();
        expect(screen.queryByText('Settings')).not.toBeInTheDocument();
      });
    });

    it('filters actions by keywords', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'config' } });

      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
        expect(screen.queryByText('New Conversation')).not.toBeInTheDocument();
      });
    });

    it('shows "No results found" for non-matching search', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'xyz123nonexistent' } });

      await waitFor(() => {
        expect(screen.getByText('No results found.')).toBeInTheDocument();
      });
    });

    it('is case-insensitive', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'SETTINGS' } });

      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });
  });

  describe('Action Groups', () => {
    it('displays actions in groups', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      await waitFor(() => {
        expect(screen.getByText('Actions')).toBeInTheDocument();
        expect(screen.getByText('Navigation')).toBeInTheDocument();
      });
    });

    it('maintains group structure when filtering', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'new' } });

      await waitFor(() => {
        // Should only show Actions group (New Conversation, Create Agent)
        expect(screen.getByText('Actions')).toBeInTheDocument();
        expect(screen.queryByText('Navigation')).not.toBeInTheDocument();
      });
    });
  });

  describe('Action Execution', () => {
    it('navigates to home when selecting New Conversation', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const newConversation = await screen.findByText('New Conversation');
      fireEvent.click(newConversation);

      expect(mockNavigate).toHaveBeenCalledWith('/');
    });

    it('navigates to settings when selecting Settings', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const settings = await screen.findByText('Settings');
      fireEvent.click(settings);

      expect(mockNavigate).toHaveBeenCalledWith('/settings');
    });

    it('closes after action execution', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const action = await screen.findByText('Settings');
      fireEvent.click(action);

      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/type a command/i)).not.toBeInTheDocument();
      });
    });

    it('copies an unhydrated trajectory from the server selected in the route', async () => {
      const fullData = {
        id: 'shared-chat',
        name: 'Secondary chat',
        log: [
          { role: 'user' as const, content: 'Hello' },
          { role: 'assistant' as const, content: 'Hi' },
        ],
      };
      conversations$.set(new Map());
      selectedConversation$.set('shared-chat');
      const secondaryClient = { getConversation: jest.fn().mockResolvedValue(fullData) };
      mockGetClientForServer.mockReturnValue(secondaryClient);
      mockGetClient.mockReturnValue(secondaryClient);

      render(
        <MemoryRouter initialEntries={['/chat/shared-chat?server=secondary']}>
          <CommandPalette />
        </MemoryRouter>
      );
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(mockGetClient).toHaveBeenCalledWith('secondary');
        expect(secondaryClient.getConversation).toHaveBeenCalledWith('shared-chat');
        expect(copyConversationToClipboard).toHaveBeenCalledWith('Secondary chat', fullData.log, {
          includeThinking: false,
          includeTools: false,
        });
      });
    });

    it('uses the route server when duplicate conversation IDs exist in the local store', async () => {
      const fullData = {
        id: 'shared-chat',
        name: 'Secondary copy',
        log: [{ role: 'user' as const, content: 'From secondary' }],
      };
      updateConversation('shared-chat', {
        data: {
          id: 'shared-chat',
          name: 'Primary copy',
          log: [{ role: 'user' as const, content: 'From primary' }],
          logfile: 'shared-chat',
          branches: {},
          workspace: '.',
        },
      });
      selectedConversation$.set('shared-chat');
      const secondaryClient = { getConversation: jest.fn().mockResolvedValue(fullData) };
      mockGetClientForServer.mockReturnValue(secondaryClient);
      mockGetClient.mockReturnValue(secondaryClient);

      render(
        <MemoryRouter initialEntries={['/chat/shared-chat?server=secondary']}>
          <CommandPalette />
        </MemoryRouter>
      );
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(mockGetClient).toHaveBeenCalledWith('secondary');
        expect(secondaryClient.getConversation).toHaveBeenCalledWith('shared-chat');
        expect(mockApi.getConversation).not.toHaveBeenCalled();
        expect(copyConversationToClipboard).toHaveBeenCalledWith('Secondary copy', fullData.log, {
          includeThinking: false,
          includeTools: false,
        });
      });
    });

    it('does not fall back to the primary server for an unknown route server', async () => {
      selectedConversation$.set('shared-chat');
      mockGetClientForServer.mockReturnValue(null);

      render(
        <MemoryRouter initialEntries={['/chat/shared-chat?server=removed-server']}>
          <CommandPalette />
        </MemoryRouter>
      );
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(mockGetClientForServer).toHaveBeenCalledWith('removed-server');
        expect(mockGetClient).not.toHaveBeenCalled();
        expect(mockApi.getConversation).not.toHaveBeenCalled();
        expect(copyConversationToClipboard).not.toHaveBeenCalled();
        expect(toast.error).toHaveBeenCalledWith('Server not found');
      });
    });

    it('copies a built-in demo trajectory from the local store', async () => {
      mockIsDemoMode.mockReturnValue(true);
      const log = [{ role: 'user' as const, content: 'Demo message' }];
      updateConversation('introduction', {
        data: {
          id: 'introduction',
          name: 'Introduction to gptme',
          log,
          logfile: 'introduction',
          branches: {},
          workspace: '/demo/workspace',
        },
      });
      selectedConversation$.set('introduction');

      renderCommandPalette();
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(mockApi.getConversation).not.toHaveBeenCalled();
        expect(copyConversationToClipboard).toHaveBeenCalledWith('Introduction to gptme', log, {
          includeThinking: false,
          includeTools: false,
        });
      });
    });

    it('fetches a server trajectory whose ID matches a built-in demo', async () => {
      const fullData = {
        id: 'introduction',
        name: 'Server introduction',
        log: [{ role: 'user' as const, content: 'From server' }],
      };
      updateConversation('introduction', {
        data: {
          id: 'introduction',
          name: 'Introduction to gptme',
          log: [{ role: 'user' as const, content: 'From demo' }],
          logfile: 'introduction',
          branches: {},
          workspace: '/demo/workspace',
        },
      });
      selectedConversation$.set('introduction');
      mockApi.getConversation.mockResolvedValue(fullData);

      render(
        <MemoryRouter initialEntries={['/chat/introduction']}>
          <CommandPalette />
        </MemoryRouter>
      );
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(mockApi.getConversation).toHaveBeenCalledWith('introduction');
        expect(copyConversationToClipboard).toHaveBeenCalledWith(
          'Server introduction',
          fullData.log,
          {
            includeThinking: false,
            includeTools: false,
          }
        );
      });
    });

    it('copies the full trajectory through the primary server', async () => {
      const fullData = {
        id: 'primary-chat',
        name: 'Primary chat',
        log: [{ role: 'user' as const, content: 'Hello' }],
      };
      selectedConversation$.set('primary-chat');
      mockApi.getConversation.mockResolvedValue(fullData);

      renderCommandPalette();
      await selectCopyCommand('Copy trajectory as Markdown (full)');

      await waitFor(() => {
        expect(mockGetClient).not.toHaveBeenCalled();
        expect(mockApi.getConversation).toHaveBeenCalledWith('primary-chat');
        expect(copyConversationToClipboard).toHaveBeenCalledWith('Primary chat', fullData.log, {
          includeThinking: true,
          includeTools: true,
        });
      });
    });

    it('reports a server fetch failure', async () => {
      selectedConversation$.set('primary-chat');
      mockApi.getConversation.mockRejectedValue(new Error('network error'));

      renderCommandPalette();
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith('Failed to copy to clipboard');
        expect(copyConversationToClipboard).not.toHaveBeenCalled();
      });
    });

    it('does not copy an empty server trajectory', async () => {
      selectedConversation$.set('primary-chat');
      mockApi.getConversation.mockResolvedValue({
        id: 'primary-chat',
        name: 'Primary chat',
        log: [],
      });

      renderCommandPalette();
      await selectCopyCommand('Copy trajectory as Markdown');

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith('No messages to copy');
        expect(copyConversationToClipboard).not.toHaveBeenCalled();
      });
    });
  });

  describe('State Management', () => {
    it('shows copy commands when conversation is selected after mount', async () => {
      // Regression test for the useMemo stale-deps bug:
      // selectedConversation$ was not in the actions deps array, so copy commands
      // never appeared when a conversation was selected after the component mounted.
      // Fix: use use$() to track the observable and include it in deps.
      selectedConversation$.set('');
      renderCommandPalette();
      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      // No conversation selected — copy commands must be absent
      await waitFor(() => {
        expect(screen.queryByText('Copy trajectory as Markdown')).not.toBeInTheDocument();
      });

      // Select a conversation post-mount — this is the case the bug missed
      act(() => {
        selectedConversation$.set('test-chat');
      });

      // Copy commands must now appear without remounting
      await waitFor(() => {
        expect(screen.getByText('Copy trajectory as Markdown')).toBeInTheDocument();
      });
    });

    it('resets search when closing', async () => {
      renderCommandPalette();

      // Open and search
      fireEvent.keyDown(document, { key: 'k', metaKey: true });
      const input = await screen.findByPlaceholderText(/type a command/i);
      fireEvent.change(input, { target: { value: 'settings' } });

      // Close
      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      // Reopen
      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      // Search should be reset
      const newInput = await screen.findByPlaceholderText(/type a command/i);
      expect(newInput).toHaveValue('');
    });
  });

  describe('Performance', () => {
    it('memoizes actions array', async () => {
      const { rerender } = renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });
      const initialActions = await screen.findByText('New Conversation');

      // Rerender without prop changes
      rerender(
        <BrowserRouter>
          <CommandPalette />
        </BrowserRouter>
      );

      // Actions should still be present (not recreated)
      expect(screen.getByText('New Conversation')).toBe(initialActions);
    });

    it('efficiently filters large result sets', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);

      // Rapid search updates should not cause performance issues
      fireEvent.change(input, { target: { value: 's' } });
      fireEvent.change(input, { target: { value: 'se' } });
      fireEvent.change(input, { target: { value: 'set' } });
      fireEvent.change(input, { target: { value: 'sett' } });

      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });
  });

  describe('Accessibility', () => {
    it('provides descriptive text for screen readers', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      const input = await screen.findByPlaceholderText(/type a command/i);
      expect(input).toHaveAttribute('placeholder', expect.stringContaining('command'));
    });

    it('displays action descriptions', async () => {
      renderCommandPalette();

      fireEvent.keyDown(document, { key: 'k', metaKey: true });

      await waitFor(() => {
        expect(screen.getByText('Start a new chat')).toBeInTheDocument();
        expect(screen.getByText('Configure application')).toBeInTheDocument();
      });
    });
  });
});
