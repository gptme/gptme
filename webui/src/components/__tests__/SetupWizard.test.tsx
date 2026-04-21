import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SetupWizard } from '../SetupWizard';
import { SettingsProvider } from '@/contexts/SettingsContext';

const mockConnect = jest.fn();
const mockOpen = jest.fn();
const mockFetch = jest.fn();
const mockInvokeTauri = jest.fn();
const isConnected$ = observable(false);
const mockIsTauriEnvironment = jest.fn(() => false);

type MockTauriServerStatus = {
  running: boolean;
  port: number;
  port_available: boolean;
  manages_local_server: boolean;
};

type MockUseTauriServerStatusResult = {
  isLoading: boolean;
  managesLocalServer: boolean | null;
  serverStatus: MockTauriServerStatus | null;
};

const mockUseTauriServerStatus = jest.fn(
  (): MockUseTauriServerStatusResult => ({
    isLoading: false,
    managesLocalServer: false,
    serverStatus: null,
  })
);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    isConnected$,
    connect: mockConnect,
    connectionConfig: {
      baseUrl: 'http://127.0.0.1:5700',
      authToken: null,
      useAuthToken: false,
    },
  }),
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => mockIsTauriEnvironment(),
  invokeTauri: (...args: unknown[]) => mockInvokeTauri(...args),
}));

jest.mock('@/hooks/useTauriServerStatus', () => ({
  useTauriServerStatus: () => mockUseTauriServerStatus(),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { get: () => unknown }) => obs.get(),
}));

jest.mock('@/components/ui/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h1>{children}</h1>,
}));

jest.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

jest.mock('@/components/ui/input', () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

jest.mock('@/components/ui/label', () => ({
  Label: ({
    children,
    ...props
  }: React.LabelHTMLAttributes<HTMLLabelElement> & { children: React.ReactNode }) => (
    <label {...props}>{children}</label>
  ),
}));

jest.mock('lucide-react', () => ({
  Monitor: () => <span>Monitor</span>,
  Cloud: () => <span>Cloud</span>,
  ArrowRight: () => <span>ArrowRight</span>,
  Check: () => <span>Check</span>,
  Terminal: () => <span>Terminal</span>,
  ExternalLink: () => <span>ExternalLink</span>,
}));

describe('SetupWizard', () => {
  beforeEach(() => {
    localStorage.clear();
    isConnected$.set(false);
    mockConnect.mockReset();
    mockOpen.mockReset();
    mockFetch.mockReset();
    mockInvokeTauri.mockReset();
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ provider_configured: true }),
    });
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
    mockIsTauriEnvironment.mockReturnValue(false);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: false,
      serverStatus: null,
    });
    Object.defineProperty(window, 'open', {
      writable: true,
      value: mockOpen,
    });
  });

  it('waits for cloud connection before showing completion', async () => {
    const { rerender } = render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /cloud/i }));
    fireEvent.click(screen.getByRole('button', { name: /sign in to gptme.ai/i }));

    expect(mockOpen).toHaveBeenCalledWith('https://fleet.gptme.ai/authorize', '_blank');
    expect(screen.getByText(/waiting for sign-in to complete/i)).toBeInTheDocument();
    expect(screen.queryByText(/you're all set/i)).not.toBeInTheDocument();

    isConnected$.set(true);
    rerender(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /you're all set/i })).toBeInTheDocument();
    });
  });

  it('marks setup complete after local connect succeeds', async () => {
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /monitor local/i }));
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(mockConnect).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('http://127.0.0.1:5700/api/v2');
    });

    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem('gptme-settings') || '{}')).toMatchObject({
        hasCompletedSetup: true,
      });
    });
    expect(screen.getByRole('heading', { name: /you're all set/i })).toBeInTheDocument();
  });

  it('shows desktop API key entry when the local server lacks a provider', async () => {
    mockIsTauriEnvironment.mockReturnValue(true);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: true,
      serverStatus: {
        running: true,
        port: 5700,
        port_available: false,
        manages_local_server: true,
      },
    });
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ provider_configured: false }),
    });
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /monitor local/i }));
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /use gptme.ai instead/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /i configured a provider/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /openrouter/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /gemini/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /deepseek/i })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('gptme-settings') || '{}')).not.toMatchObject({
      hasCompletedSetup: true,
    });
  });

  it('keeps the cloud step visible when switching from provider fallback', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ provider_configured: false }),
    });
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /monitor local/i }));
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /use gptme.ai instead/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /cloud setup/i })).toBeInTheDocument();
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(
      screen.queryByRole('heading', { name: /configure a provider/i })
    ).not.toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('connects to a remote server during tauri mobile setup', async () => {
    mockIsTauriEnvironment.mockReturnValue(true);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: false,
      serverStatus: {
        running: false,
        port: 5700,
        port_available: false,
        manages_local_server: false,
      },
    });
    mockConnect.mockResolvedValue(undefined);

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /remote server/i }));
    fireEvent.change(screen.getByPlaceholderText('https://bob.example.com'), {
      target: { value: 'https://bob.example.com/' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional API token'), {
      target: { value: 'secret-token' },
    });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(mockConnect).toHaveBeenCalledWith({
        baseUrl: 'https://bob.example.com',
        authToken: 'secret-token',
        useAuthToken: true,
      });
    });
  });

  it('waits for tauri status before enabling the server mode choice', () => {
    mockIsTauriEnvironment.mockReturnValue(true);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: true,
      managesLocalServer: null,
      serverStatus: null,
    });

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));

    expect(screen.getByRole('button', { name: /monitor checking environment/i })).toBeDisabled();
  });

  it('saves an API key via the server API and advances to complete', async () => {
    mockIsTauriEnvironment.mockReturnValue(true);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: true,
      serverStatus: {
        running: true,
        port: 5700,
        port_available: false,
        manages_local_server: true,
      },
    });
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ provider_configured: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ status: 'ok', env_var: 'ANTHROPIC_API_KEY' }),
      })
      .mockRejectedValueOnce(new Error('connection refused'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ provider_configured: true }),
      });
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });
    let startAttempts = 0;
    mockInvokeTauri.mockImplementation(async (cmd: string) => {
      if (cmd === 'start_server' && startAttempts++ === 0) {
        throw new Error('Port 5700 is already in use');
      }
    });

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /monitor local/i }));
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-ant-test-key' } });
    fireEvent.click(screen.getByRole('button', { name: /save and restart server/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('http://127.0.0.1:5700/api/v2/user/api-key', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          provider: 'anthropic',
          api_key: 'sk-ant-test-key',
        }),
      });
    });
    expect(mockInvokeTauri).toHaveBeenCalledWith('stop_server');
    expect(mockInvokeTauri).toHaveBeenCalledWith('start_server');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /you're all set/i })).toBeInTheDocument();
    });
  });

  it('shows an error instead of falsely completing when the restarted server never comes back', async () => {
    mockIsTauriEnvironment.mockReturnValue(true);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: true,
      serverStatus: {
        running: true,
        port: 5700,
        port_available: false,
        manages_local_server: true,
      },
    });
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ provider_configured: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ status: 'ok', env_var: 'ANTHROPIC_API_KEY' }),
      })
      .mockRejectedValue(new Error('connection refused'));
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });
    mockInvokeTauri.mockResolvedValue(undefined);

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /monitor local/i }));
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-ant-test-key' } });
    fireEvent.click(screen.getByRole('button', { name: /save and restart server/i }));

    await waitFor(
      () => {
        expect(screen.getByText(/server did not come back in time/i)).toBeInTheDocument();
      },
      { timeout: 4000 }
    );
    expect(screen.queryByRole('heading', { name: /you're all set/i })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
  });

  it('surfaces API key save errors without advancing', async () => {
    mockIsTauriEnvironment.mockReturnValue(true);
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: true,
      serverStatus: {
        running: true,
        port: 5700,
        port_available: false,
        manages_local_server: true,
      },
    });
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ provider_configured: false }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ error: 'Failed to write config: permission denied' }),
      });
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });

    render(
      <SettingsProvider>
        <SetupWizard />
      </SettingsProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /get started/i }));
    fireEvent.click(screen.getByRole('button', { name: /monitor local/i }));
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-bad' } });
    fireEvent.click(screen.getByRole('button', { name: /save and restart server/i }));

    await waitFor(() => {
      expect(screen.getByText(/permission denied/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    expect(mockInvokeTauri).not.toHaveBeenCalledWith('stop_server');
    expect(mockInvokeTauri).not.toHaveBeenCalledWith('start_server');
  });
});
