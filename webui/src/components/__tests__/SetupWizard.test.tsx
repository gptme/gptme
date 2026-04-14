import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { SetupWizard } from '../SetupWizard';
import { SettingsProvider } from '@/contexts/SettingsContext';

const mockConnect = jest.fn();
const mockOpen = jest.fn();
const isConnected$ = observable(false);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    isConnected$,
    connect: mockConnect,
  }),
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => false,
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
    mockConnect.mockReset();
    mockOpen.mockReset();
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
    mockConnect.mockResolvedValue(undefined);

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

    expect(JSON.parse(localStorage.getItem('gptme-settings') || '{}')).toMatchObject({
      hasCompletedSetup: true,
    });
    expect(screen.getByRole('heading', { name: /you're all set/i })).toBeInTheDocument();
  });
});
