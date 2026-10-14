import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { EmbeddedMenuItem } from '@/lib/embeddedContext';
import { shortcutsDialogOpen$ } from '@/stores/shortcutsDialog';
import { observable } from '@legendapp/state';
import { MenuBar } from '../MenuBar';
import { ShortcutsDialog } from '../ShortcutsDialog';
import { settingsModal$ } from '@/stores/settingsModal';

const compatibilityWarning$ = observable<null | {
  kind: 'server_older' | 'api_major_mismatch';
  serverApiVersion: number;
  serverContractRevision: number;
  clientApiVersion: number;
  minimumContractRevision: number;
}>(null);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ api: { compatibilityWarning$ } }),
}));

let mockIsEmbedded = false;
let mockMenuItems: EmbeddedMenuItem[] = [];
const mockSendAction = jest.fn();

jest.mock('@/contexts/EmbeddedContext', () => ({
  useEmbeddedContext: () => ({
    menuItems: mockMenuItems,
    sendAction: mockSendAction,
    isEmbedded: mockIsEmbedded,
  }),
}));

beforeEach(() => {
  mockIsEmbedded = false;
  mockMenuItems = [];
  mockSendAction.mockClear();
  shortcutsDialogOpen$.set(false);
  compatibilityWarning$.set(null);
});

jest.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/chat">{children}</a>,
}));

jest.mock('@/utils/routes', () => ({
  appRoute: (path: string) => path,
}));

jest.mock('../ServerSelector', () => ({
  ServerSelector: () => <div data-testid="server-selector" />,
}));

describe('MenuBar compatibility warning', () => {
  beforeEach(() => {
    compatibilityWarning$.set(null);
    settingsModal$.set({ open: false, category: 'appearance' });
  });

  it('stays hidden for compatible servers', () => {
    render(<MenuBar />);

    expect(
      screen.queryByRole('button', { name: 'Server compatibility warning' })
    ).not.toBeInTheDocument();
  });

  it('remains visible across routes and opens server settings', () => {
    compatibilityWarning$.set({
      kind: 'server_older',
      serverApiVersion: 2,
      serverContractRevision: 0,
      clientApiVersion: 2,
      minimumContractRevision: 1,
    });

    render(<MenuBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Server compatibility warning' }));

    expect(settingsModal$.open.get()).toBe(true);
    expect(settingsModal$.category.get()).toBe('servers');
  });
});

describe('MenuBar keyboard shortcuts and embedded menu', () => {
  const hostItems: EmbeddedMenuItem[] = [
    { kind: 'link', id: 'docs', label: 'Documentation', href: '/docs', section: 'General' },
    {
      kind: 'link',
      id: 'account',
      label: 'Account settings',
      href: '/account',
      section: 'Account',
    },
    { kind: 'link', id: 'feedback', label: 'Feedback', href: '/feedback', section: ' General ' },
    {
      kind: 'action',
      id: 'logout',
      label: 'Sign out',
      action: 'logout',
      section: 'Account',
      destructive: true,
    },
  ];

  it.each([false, true])(
    'keeps a working topbar button without a host menu (embedded=%s)',
    async (embedded) => {
      mockIsEmbedded = embedded;
      const user = userEvent.setup();
      render(<MenuBar />);

      await user.click(screen.getByRole('button', { name: 'Keyboard shortcuts' }));

      expect(shortcutsDialogOpen$.get()).toBe(true);
      expect(screen.queryByRole('button', { name: 'Account menu' })).not.toBeInTheDocument();
      expect(mockSendAction).not.toHaveBeenCalled();
    }
  );

  it.each(['Escape', 'Close'])(
    'opens and focuses the real shortcuts dialog from the account menu, then dismisses with %s',
    async (dismissal) => {
      mockIsEmbedded = true;
      mockMenuItems = hostItems;
      const user = userEvent.setup();
      render(
        <>
          <MenuBar />
          <ShortcutsDialog />
        </>
      );

      expect(screen.queryByRole('button', { name: 'Keyboard shortcuts' })).not.toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Account menu' }));
      await user.click(screen.getByRole('menuitem', { name: 'Keyboard shortcuts' }));

      expect(shortcutsDialogOpen$.get()).toBe(true);
      expect(mockSendAction).not.toHaveBeenCalled();
      expect(screen.queryByRole('menu')).not.toBeInTheDocument();
      const dialog = await screen.findByRole('dialog', { name: 'Keyboard shortcuts' });
      expect(dialog).toBeVisible();
      await waitFor(() =>
        expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus()
      );

      if (dismissal === 'Escape') {
        await user.keyboard('{Escape}');
      } else {
        await user.click(within(dialog).getByRole('button', { name: 'Close' }));
      }

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(shortcutsDialogOpen$.get()).toBe(false);
    }
  );

  it('adds a local General section when the host supplies only Account items', async () => {
    mockIsEmbedded = true;
    mockMenuItems = hostItems.filter((item) => item.section === 'Account');
    const user = userEvent.setup();
    render(<MenuBar />);
    await user.click(screen.getByRole('button', { name: 'Account menu' }));

    const menu = screen.getByRole('menu');
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((item) => item.textContent)
    ).toEqual(['Account settings', 'Sign out', 'Keyboard shortcuts']);
    expect(within(menu).getAllByText('General')).toHaveLength(1);
    await user.click(within(menu).getByRole('menuitem', { name: 'Keyboard shortcuts' }));
    expect(shortcutsDialogOpen$.get()).toBe(true);
    expect(mockSendAction).not.toHaveBeenCalled();
  });

  it('ignores host menu items outside embedded mode', async () => {
    mockMenuItems = hostItems;
    const user = userEvent.setup();
    render(<MenuBar />);

    expect(screen.queryByRole('button', { name: 'Account menu' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Keyboard shortcuts' }));
    expect(shortcutsDialogOpen$.get()).toBe(true);
    expect(mockSendAction).not.toHaveBeenCalled();
  });

  it('coalesces repeated sections in first-seen order while retaining item order and host behavior', async () => {
    mockIsEmbedded = true;
    mockMenuItems = hostItems;
    const user = userEvent.setup();
    render(<MenuBar />);
    await user.click(screen.getByRole('button', { name: 'Account menu' }));

    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByText('General')).toHaveLength(1);
    expect(within(menu).getAllByText('Account')).toHaveLength(1);
    expect(within(menu).getAllByRole('separator')).toHaveLength(1);
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((item) => item.textContent)
    ).toEqual(['Documentation', 'Feedback', 'Keyboard shortcuts', 'Account settings', 'Sign out']);
    expect(within(menu).getByRole('menuitem', { name: 'Documentation' })).toHaveAttribute(
      'href',
      '/docs'
    );
    expect(within(menu).getByRole('menuitem', { name: 'Documentation' })).toHaveAttribute(
      'target',
      '_top'
    );
    const signOut = within(menu).getByRole('menuitem', { name: 'Sign out' });
    expect(signOut).toHaveClass('text-destructive');
    await user.click(signOut);
    expect(mockSendAction).toHaveBeenCalledWith('logout', 'logout');
    expect(shortcutsDialogOpen$.get()).toBe(false);
  });
});
