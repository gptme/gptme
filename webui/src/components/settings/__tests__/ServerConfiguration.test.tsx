import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ServerConfiguration } from '../ServerConfiguration';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockRegistry = {
  servers: [
    {
      id: 'local',
      name: 'Local',
      baseUrl: 'http://127.0.0.1:5700',
      authToken: null,
      useAuthToken: false,
      isPreset: true,
      createdAt: 1,
      lastUsedAt: 1,
    },
  ],
  activeServerId: 'local',
  connectedServerIds: ['local'],
};

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    connect: jest.fn(),
    switchServer: jest.fn(() => Promise.resolve()),
  }),
}));

jest.mock('@/hooks/useUserSettings', () => ({
  useUserSettings: () => ({ settings: null }),
}));

jest.mock('@/stores/servers', () => ({
  serverRegistry$: { get: () => mockRegistry },
  addServer: jest.fn(),
  updateServer: jest.fn(),
  removeServer: jest.fn(),
  connectServer: jest.fn(),
  disconnectServer: jest.fn(),
}));

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: jest.fn(() => null),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { get: () => unknown } | null) => (obs ? obs.get() : null),
}));

jest.mock('../ConfigFileEditor', () => ({ ConfigFileEditor: () => null }));
jest.mock('../ServerDefaultModelSettings', () => ({ ServerDefaultModelSettings: () => null }));
jest.mock('../ServerApiKeySettings', () => ({ ServerApiKeySettings: () => null }));
jest.mock('../ServerProviderHealthSettings', () => ({ ServerProviderHealthSettings: () => null }));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

describe('ServerConfiguration', () => {
  it('focuses and marks the server URL invalid when submitted empty', async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <ServerConfiguration />
      </TooltipProvider>
    );

    await user.click(screen.getByRole('button', { name: 'Add Server' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add & Connect' }));

    const urlInput = screen.getByRole('textbox', { name: 'Server URL' });
    await waitFor(() => expect(urlInput).toHaveFocus());
    expect(urlInput).toHaveAttribute('aria-invalid', 'true');
    expect(urlInput).toHaveAccessibleDescription('Server URL is required');
  });
});
