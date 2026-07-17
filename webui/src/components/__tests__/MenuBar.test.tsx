import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { MenuBar } from '../MenuBar';
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

jest.mock('@/contexts/EmbeddedContext', () => ({
  useEmbeddedContext: () => ({ menuItems: [], sendAction: jest.fn(), isEmbedded: false }),
}));

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
