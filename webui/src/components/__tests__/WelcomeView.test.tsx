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
const lastConnectionResult$ = observable<null | {
  ok: false;
  url: string;
  reason: 'network' | 'http_error' | 'parse_error' | 'timeout' | 'cors';
  message: string;
}>(null);
let mockBaseUrl = 'http://localhost:5700';
// Controls whether isLikelyChromeCorsPna() classifies the last probe URL as PNA.
const mockIsLikelyChromeCorsPna = jest.fn().mockReturnValue(false);

const setLocation = (href: string) => {
  const url = new URL(href);
  Object.defineProperty(window, 'location', {
    value: {
      ...window.location,
      href: url.href,
      origin: url.origin,
      hostname: url.hostname,
    },
    writable: true,
    configurable: true,
  });
};

jest.mock('@/utils/api', () => ({
  isLikelyChromeCorsPna: (...args: unknown[]) => mockIsLikelyChromeCorsPna(...args),
}));

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
        lastConnectionResult$,
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
    setLocation('http://localhost/');
    isConnected$.set(true);
    lastConnectionResult$.set(null);
    mockBaseUrl = 'http://localhost:5700';
    setupWizard$.step.set('welcome');
    setupWizard$.open.set(false);
    setupWizard$.providerStatusVersion.set(0);
    settingsModal$.set({ open: false, category: 'appearance' });
    mockConnect.mockReset();
    mockNavigate.mockClear();
    mockInvalidateQueries.mockClear();
    mockFetch.mockReset();
    mockIsLikelyChromeCorsPna.mockReturnValue(false);
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

  it('detects a reachable hosted loopback server and shows CORS setup guidance before retry', async () => {
    seedReturningUser();
    setLocation('https://chat.gptme.org/');
    isConnected$.set(false);
    // Simulate: CORS fetch blocked (server running, no --cors-origin), no-cors probe succeeds.
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce({});

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(
      await screen.findByText(/appears to be running, but it is not allowing requests from/i)
    ).toBeInTheDocument();
    expect(screen.getByText('Local gptme server needs browser access')).toBeInTheDocument();
    expect(
      screen.getByText(/gptme-server --cors-origin='https:\/\/chat\.gptme\.org'/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/Install it, then start a server/i)).not.toBeInTheDocument();
    // First call: CORS probe (no mode: no-cors).
    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      'http://localhost:5700/api/v2',
      expect.objectContaining({ cache: 'no-store' })
    );
    // Second call: no-cors probe confirms server is running but CORS is blocking.
    expect(mockFetch).toHaveBeenNthCalledWith(
      2,
      'http://localhost:5700/api/v2',
      expect.objectContaining({
        mode: 'no-cors',
        cache: 'no-store',
        targetAddressSpace: 'local',
      })
    );
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

  it('shows "server not running" guidance for a network/refused error on the default local server', () => {
    seedReturningUser();
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://localhost:5700/api/v2',
      reason: 'network',
      message: 'Could not reach server (connection refused or no DNS)',
    });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText(/The gptme server is not running/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/Start a local gptme server or point the app/i)
    ).not.toBeInTheDocument();
  });

  it('shows "connection refused" guidance for a network error on a custom server', () => {
    mockBaseUrl = 'http://my-server.example.com:5700';
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://my-server.example.com:5700/api/v2',
      reason: 'network',
      message: 'Could not reach server',
    });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText(/Connection refused/i)).toBeInTheDocument();
    expect(screen.queryByText(/Check the server URL and auth token/i)).not.toBeInTheDocument();
  });

  it('shows CORS guidance and the cors-origin command for a cors error on a custom server', () => {
    mockBaseUrl = 'http://my-server.example.com:5700';
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://my-server.example.com:5700/api/v2',
      reason: 'cors',
      message: 'CORS error',
    });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText(/rejected cross-origin requests/i)).toBeInTheDocument();
    // The serverCommand code block contains the full gptme-server invocation
    expect(screen.getByText(/gptme-server --cors-origin/i)).toBeInTheDocument();
  });

  it('shows PNA guidance when Chrome Local Network Access blocks the connection', () => {
    mockIsLikelyChromeCorsPna.mockReturnValue(true);
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://localhost:5700/api/v2',
      reason: 'cors',
      message: 'CORS error',
    });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(
      screen.getByText(/Chrome blocked this connection.*Local Network Access/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/rejected cross-origin requests/i)).not.toBeInTheDocument();

    mockIsLikelyChromeCorsPna.mockReturnValue(false);
  });

  it('shows timeout guidance for a timeout error', () => {
    seedReturningUser();
    isConnected$.set(false);
    lastConnectionResult$.set({
      ok: false,
      url: 'http://localhost:5700/api/v2',
      reason: 'timeout',
      message: 'Request timed out after 3s',
    });

    render(
      <SettingsProvider>
        <WelcomeView />
      </SettingsProvider>
    );

    expect(screen.getByText(/connection timed out/i)).toBeInTheDocument();
    expect(screen.queryByText(/Start a local gptme server/i)).not.toBeInTheDocument();
  });
});
