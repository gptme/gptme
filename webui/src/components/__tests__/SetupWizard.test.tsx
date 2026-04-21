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
let isTauriMock = false;

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    isConnected$,
    connect: mockConnect,
    connectionConfig: { baseUrl: 'http://localhost:5700' },
  }),
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => isTauriMock,
  invokeTauri: (...args: unknown[]) => mockInvokeTauri(...args),
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
    isTauriMock = false;
    mockConnect.mockReset();
    mockOpen.mockReset();
    mockFetch.mockReset();
    mockInvokeTauri.mockReset();
    mockFetch.mockResolvedValue({
      json: async () => ({ provider_configured: true }),
    });
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
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
      expect(mockFetch).toHaveBeenCalledWith('http://localhost:5700/api/v2');
    });

    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem('gptme-settings') || '{}')).toMatchObject({
        hasCompletedSetup: true,
      });
    });
    expect(screen.getByRole('heading', { name: /you're all set/i })).toBeInTheDocument();
  });

  it('shows provider setup guidance when connected server is in degraded mode', async () => {
    mockFetch.mockResolvedValue({
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
    expect(JSON.parse(localStorage.getItem('gptme-settings') || '{}')).not.toMatchObject({
      hasCompletedSetup: true,
    });
  });

  it('keeps the cloud step visible when switching from provider fallback', async () => {
    mockFetch.mockResolvedValue({
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

  it('saves an API key via Tauri and advances to complete', async () => {
    isTauriMock = true;
    // First /api/v2 call: no provider. After save + restart: provider configured.
    mockFetch
      .mockResolvedValueOnce({ json: async () => ({ provider_configured: false }) })
      .mockResolvedValueOnce({ json: async () => ({ provider_configured: true }) });
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

    const input = screen.getByLabelText(/api key/i);
    fireEvent.change(input, { target: { value: 'sk-ant-test-key' } });

    fireEvent.click(screen.getByRole('button', { name: /save and restart server/i }));

    await waitFor(() => {
      expect(mockInvokeTauri).toHaveBeenCalledWith('save_api_key', {
        provider: 'anthropic',
        apiKey: 'sk-ant-test-key',
      });
    });
    // Server restart and provider recheck should follow.
    expect(mockInvokeTauri).toHaveBeenCalledWith('stop_server');
    expect(mockInvokeTauri).toHaveBeenCalledWith('start_server');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /you're all set/i })).toBeInTheDocument();
    });
  });

  it('surfaces save_api_key errors without advancing', async () => {
    isTauriMock = true;
    mockFetch.mockResolvedValue({ json: async () => ({ provider_configured: false }) });
    mockConnect.mockImplementation(async () => {
      isConnected$.set(true);
    });
    mockInvokeTauri.mockImplementation(async (cmd: string) => {
      if (cmd === 'save_api_key') throw new Error('Failed to write config: permission denied');
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
    // Still on provider step.
    expect(screen.getByRole('heading', { name: /configure a provider/i })).toBeInTheDocument();
    // Should not have tried to restart the server.
    expect(mockInvokeTauri).not.toHaveBeenCalledWith('stop_server');
    expect(mockInvokeTauri).not.toHaveBeenCalledWith('start_server');
  });
});
