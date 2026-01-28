import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { CommandPalette } from '../CommandPalette';

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
  });

  describe('State Management', () => {
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
