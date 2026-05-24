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
let mockBaseUrl = 'http://localhost:5700';

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
      connectionConfig: { baseUrl: mockBaseUrl },
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
  // Returning users (who have completed the setup wizard) see the efficient
  // raw install/settings path; first-time users get the guided "Get started" CTA.
  const seedReturningUser = () => {
    localStorage.setItem('gptme-settings', JSON.stringify({ hasCompletedSetup: true }));
  };

  beforeEach(() => {
    // Default to a first-time user (no stored settings → hasCompletedSetup=false).
    localStorage.clear();
    isConnected$.set(true);
    mockBaseUrl = 'http://localhost:5700';
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
    seedReturningUser();
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
    // Brand-new users need the install step before the server command (#2479)
    expect(screen.getByText("pipx install 'gptme[server]'")).toBeInTheDocument();
    expect(
      screen.getByText(/New to gptme\? Install it, then start a server:/i)
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry connection/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy start command/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /server settings/i })).toBeInTheDocument();
    // Cloud alternative completes the two-path onboarding story (#2485)
    expect(screen.getByRole('button', { name: /use gptme\.ai/i })).toBeInTheDocument();
    // Setup-guide link points at the canonical server/CORS docs (#2479, #2478)
    expect(screen.getByRole('link', { name: /server setup guide/i })).toHaveAttribute(
      'href',
      'https://gptme.org/docs/server.html'
    );
  });

  it('opens the setup wizard at the cloud step from the disconnected banner', async () => {
    seedReturningUser();
    isConnected$.set(false);

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /use gptme\.ai/i }));

    await waitFor(() => {
      expect(setupWizard$.get()).toMatchObject({ open: true, step: 'cloud' });
    });
  });

  it('shows a guided "Get started" CTA for first-time users on the default local server', () => {
    // No seeded settings → first-time user (hasCompletedSetup=false).
    isConnected$.set(false);

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText('No gptme server connected')).toBeInTheDocument();
    // Guided path: a "Get started" CTA and a wizard-oriented intro line
    expect(screen.getByRole('button', { name: /get started/i })).toBeInTheDocument();
    expect(screen.getByText(/The setup guide walks you through/i)).toBeInTheDocument();
    // Returning-user clutter is hidden for first-timers
    expect(screen.queryByText("pipx install 'gptme[server]'")).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /copy start command/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /server settings/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /use gptme\.ai/i })).not.toBeInTheDocument();
    // Retry connection stays available regardless of onboarding state
    expect(screen.getByRole('button', { name: /retry connection/i })).toBeInTheDocument();
  });

  it('opens the setup wizard at the welcome step from the first-visit "Get started" CTA', async () => {
    isConnected$.set(false);

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));

    await waitFor(() => {
      expect(setupWizard$.get()).toMatchObject({ open: true, step: 'welcome' });
    });
  });

  it('shows docs link (but not install step) when a non-default server is disconnected', () => {
    mockBaseUrl = 'http://my-server.example.com:5700';
    isConnected$.set(false);

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    // Non-default path: different title and description
    expect(screen.getByText(/Cannot reach/i)).toBeInTheDocument();
    expect(screen.getByText(/Check the server URL and auth token/i)).toBeInTheDocument();
    // Install step is only for new users on the default local server
    expect(screen.queryByText("pipx install 'gptme[server]'")).not.toBeInTheDocument();
    expect(screen.queryByText(/New to gptme\?/i)).not.toBeInTheDocument();
    // Cloud CTA is local-server-only; a custom server user already chose their path
    expect(screen.queryByRole('button', { name: /use gptme\.ai/i })).not.toBeInTheDocument();
    // Docs link is always shown for any disconnected state (CORS/auth help applies too)
    expect(screen.getByRole('link', { name: /server setup guide/i })).toHaveAttribute(
      'href',
      'https://gptme.org/docs/server.html'
    );
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
