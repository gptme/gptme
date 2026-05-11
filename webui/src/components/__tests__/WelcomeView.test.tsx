import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SettingsProvider } from '@/contexts/SettingsContext';
import { WelcomeView } from '../WelcomeView';
import { setupWizard$ } from '@/stores/setupWizard';
import { settingsModal$ } from '@/stores/settingsModal';

const mockNavigate = jest.fn();
const mockInvalidateQueries = jest.fn();
const mockConnect = jest.fn();
const mockFetch = jest.fn();
const isConnected$ = observable(true);

jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

jest.mock('@/contexts/ApiContext', () => {
  return {
    useApi: () => ({
      api: {
        createConversationWithPlaceholder: jest.fn(),
        authHeader: null,
      },
      isConnected$,
      connect: mockConnect,
      connectionConfig: { baseUrl: 'http://localhost:5700' },
      switchServer: jest.fn(),
    }),
  };
});

jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({
    invalidateQueries: mockInvalidateQueries,
  }),
}));

jest.mock('@/stores/servers', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return {
    serverRegistry$: observable({
      servers: [{ id: 'default', name: 'Default' }],
      activeServerId: 'default',
    }),
    getConnectedServers: () => [{ id: 'default', name: 'Default' }],
  };
});

jest.mock('../ChatInput', () => ({
  ChatInput: () => <div data-testid="chat-input" />,
}));

jest.mock('../ExamplesSection', () => ({
  ExamplesSection: () => <div data-testid="examples-section" />,
}));

describe('WelcomeView', () => {
  beforeEach(() => {
    isConnected$.set(true);
    setupWizard$.step.set('welcome');
    setupWizard$.open.set(false);
    setupWizard$.providerStatusVersion.set(0);
    settingsModal$.set({ open: false, category: 'appearance' });
    mockConnect.mockReset();
    mockNavigate.mockClear();
    mockInvalidateQueries.mockClear();
    mockFetch.mockReset();
    mockFetch.mockImplementation(() => new Promise(() => {}));
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  it('renders the refreshed new chat copy and quick suggestions', () => {
    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByRole('heading', { name: 'What are you working on?' })).toBeInTheDocument();
    expect(
      screen.getByText(/Start with a real task, question, or rough idea\./)
    ).toBeInTheDocument();
    expect(screen.getByText('Try one of these')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Write a Python script' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show history' })).toBeInTheDocument();
    expect(screen.queryByText('How can I help you today?')).not.toBeInTheDocument();
  });

  it('shows an actionable disconnected-state banner when no server is connected', () => {
    isConnected$.set(false);

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText('No gptme server connected')).toBeInTheDocument();
    expect(
      screen.getByText(/Start a local gptme server or point the app at another server/i)
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry connection/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy start command/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /server settings/i })).toBeInTheDocument();
  });

  it('shows a finish-setup banner when the server has no provider configured', async () => {
    let resolveFetch:
      | ((value: { ok: boolean; json: () => Promise<{ provider_configured: boolean }> }) => void)
      | null = null;
    mockFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        })
    );

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    await act(async () => {
      resolveFetch?.({
        ok: true,
        json: async () => ({ provider_configured: false }),
      });
      await Promise.resolve();
    });

    expect(await screen.findByText('Provider setup required')).toBeInTheDocument();
    expect(
      screen.getByText(/it does not have an LLM provider configured yet/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /finish setup/i }));

    await waitFor(() => {
      expect(setupWizard$.get()).toMatchObject({ open: true, step: 'provider' });
    });
  });

  it('opens server settings from the provider-required banner', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ provider_configured: false }),
    });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(await screen.findByText('Provider setup required')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /server settings/i }));

    await waitFor(() => {
      expect(settingsModal$.get()).toMatchObject({ open: true, category: 'servers' });
    });
  });

  it('rechecks provider status after setup completion', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ provider_configured: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ provider_configured: true }),
      });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(await screen.findByText('Provider setup required')).toBeInTheDocument();

    act(() => {
      setupWizard$.providerStatusVersion.set(1);
    });

    await waitFor(() => {
      expect(screen.queryByText('Provider setup required')).not.toBeInTheDocument();
    });
  });
});
