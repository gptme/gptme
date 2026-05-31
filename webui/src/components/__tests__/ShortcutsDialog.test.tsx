import { render, screen, fireEvent } from '@testing-library/react';
import { ShortcutsDialog } from '../ShortcutsDialog';
import { shortcutsDialogOpen$ } from '@/stores/shortcutsDialog';

// Mock the store with a tiny in-memory observable so opens/changes propagate.
jest.mock('@/stores/shortcutsDialog', () => {
  let value = false;
  const listeners: Array<(arg: { value: boolean }) => void> = [];
  return {
    shortcutsDialogOpen$: {
      get: () => value,
      set: (v: boolean) => {
        value = v;
        listeners.forEach((cb) => cb({ value }));
      },
      onChange: (cb: (arg: { value: boolean }) => void) => {
        listeners.push(cb);
        return () => {
          const i = listeners.indexOf(cb);
          if (i >= 0) listeners.splice(i, 1);
        };
      },
    },
  };
});

// Render Dialog content only when open, mirroring the ui/command test pattern.
jest.mock('../ui/dialog', () => ({
  Dialog: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
    open ? <div data-testid="shortcuts-dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}));

describe('ShortcutsDialog', () => {
  beforeEach(() => {
    shortcutsDialogOpen$.set(false);
  });

  it('is closed by default', () => {
    render(<ShortcutsDialog />);
    expect(screen.queryByTestId('shortcuts-dialog')).not.toBeInTheDocument();
  });

  it('opens when the "?" key is pressed', () => {
    render(<ShortcutsDialog />);
    fireEvent.keyDown(document, { key: '?' });
    expect(screen.getByTestId('shortcuts-dialog')).toBeInTheDocument();
    expect(screen.getByText('Keyboard shortcuts')).toBeInTheDocument();
  });

  it('lists shortcut groups and descriptions', () => {
    render(<ShortcutsDialog />);
    fireEvent.keyDown(document, { key: '?' });
    expect(screen.getByText('General')).toBeInTheDocument();
    expect(screen.getByText('Open command palette')).toBeInTheDocument();
    expect(screen.getByText('Focus the message input')).toBeInTheDocument();
    expect(screen.getByText('Send message')).toBeInTheDocument();
  });

  it('does not open when "?" is typed inside an input', () => {
    render(
      <>
        <input data-testid="text-field" />
        <ShortcutsDialog />
      </>
    );
    const input = screen.getByTestId('text-field');
    fireEvent.keyDown(input, { key: '?' });
    expect(screen.queryByTestId('shortcuts-dialog')).not.toBeInTheDocument();
  });
});
