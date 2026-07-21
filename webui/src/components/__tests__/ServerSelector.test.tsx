import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { isDefaultPreset } from '../ServerSelector';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { ServerConfig, ServerRegistry } from '@/types/servers';

// ── Shared mock state ──────────────────────────────────────────────────────

let mockIsEmbedded = false;
let mockRegistry: ServerRegistry = {
  servers: [],
  activeServerId: '',
  connectedServerIds: [],
};

// ── Mocks ──────────────────────────────────────────────────────────────────

const mockSwitchServer = jest.fn(() => Promise.resolve());

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    switchServer: mockSwitchServer,
    isConnected$: { get: () => false },
    isConnecting$: { get: () => false },
    isAutoConnecting$: { get: () => false },
    stopAutoConnect: jest.fn(),
  }),
}));

jest.mock('@/contexts/EmbeddedContext', () => ({
  useEmbeddedContext: () => ({ isEmbedded: mockIsEmbedded }),
}));

jest.mock('@/stores/servers', () => ({
  serverRegistry$: { get: () => mockRegistry },
  addServer: jest.fn(),
  connectServer: jest.fn(),
  disconnectServer: jest.fn(),
}));

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: jest.fn(() => null),
}));

jest.mock('../SettingsModal', () => ({
  settingsModal$: { set: jest.fn() },
}));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

// Make use$() call .get() on the observable so it works without a Provider.
jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { get: () => unknown } | null) => (obs ? obs.get() : null),
}));

// ── isDefaultPreset unit tests ─────────────────────────────────────────────

describe('isDefaultPreset', () => {
  it('returns true for the default Local preset', () => {
    expect(isDefaultPreset({ isPreset: true, baseUrl: 'http://127.0.0.1:5700' })).toBe(true);
  });

  it('returns true with trailing slash', () => {
    expect(isDefaultPreset({ isPreset: true, baseUrl: 'http://127.0.0.1:5700/' })).toBe(true);
  });

  it('returns false for a preset with a different URL', () => {
    expect(isDefaultPreset({ isPreset: true, baseUrl: 'http://myserver.local:5700' })).toBe(false);
  });

  it('returns false for a non-preset at the default URL', () => {
    expect(isDefaultPreset({ isPreset: false, baseUrl: 'http://127.0.0.1:5700' })).toBe(false);
  });

  it('returns false for a fleet/cloud server', () => {
    expect(
      isDefaultPreset({
        isPreset: false,
        baseUrl: 'https://fleet.gptme.ai/api/v1/instances/abc',
      })
    ).toBe(false);
  });

  it('returns true for a migrated preset stored as localhost:5700', () => {
    expect(isDefaultPreset({ isPreset: true, baseUrl: 'http://localhost:5700' })).toBe(true);
  });

  it('returns true for localhost:5700 with trailing slash', () => {
    expect(isDefaultPreset({ isPreset: true, baseUrl: 'http://localhost:5700/' })).toBe(true);
  });
});

// ── ServerSelector render behaviour ───────────────────────────────────────

import { ServerSelector } from '../ServerSelector';

const LOCAL_PRESET: ServerConfig = {
  id: 'local',
  name: 'Local',
  baseUrl: 'http://127.0.0.1:5700',
  authToken: null,
  useAuthToken: false,
  isPreset: true,
  createdAt: 1,
  lastUsedAt: 1,
};

const FLEET_SERVER: ServerConfig = {
  id: 'fleet-1',
  name: 'Cloud',
  baseUrl: 'https://fleet.gptme.ai/api/v1/instances/abc',
  authToken: 'tok',
  useAuthToken: true,
  createdAt: 2,
  lastUsedAt: 2,
};

describe('ServerSelector in embedded (hosted) mode', () => {
  afterEach(() => {
    mockIsEmbedded = false;
    mockSwitchServer.mockClear();
  });

  it('renders nothing when the only server is the default Local preset', () => {
    mockIsEmbedded = true;
    mockRegistry = {
      servers: [LOCAL_PRESET],
      activeServerId: 'local',
      connectedServerIds: ['local'],
    };
    const { container } = render(<ServerSelector />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when there are no servers at all', () => {
    mockIsEmbedded = true;
    mockRegistry = { servers: [], activeServerId: '', connectedServerIds: [] };
    const { container } = render(<ServerSelector />);
    expect(container.firstChild).toBeNull();
  });

  it('renders fleet server (without Local) when both exist', () => {
    mockIsEmbedded = true;
    mockRegistry = {
      servers: [LOCAL_PRESET, FLEET_SERVER],
      activeServerId: 'fleet-1',
      connectedServerIds: ['fleet-1'],
    };
    const { container } = render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    expect(container.firstChild).not.toBeNull();
  });

  it('shows fleet server label when Local is active but hidden', () => {
    mockIsEmbedded = true;
    mockRegistry = {
      servers: [LOCAL_PRESET, FLEET_SERVER],
      activeServerId: 'local', // Local is still the stored primary
      connectedServerIds: ['local'],
    };
    const { getByText } = render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    // The trigger should show the fleet server name, not the hidden "Local"
    expect(getByText('Cloud')).toBeTruthy();
  });

  it('auto-calls switchServer to reconcile registry when Local is active but hidden', () => {
    mockIsEmbedded = true;
    mockRegistry = {
      servers: [LOCAL_PRESET, FLEET_SERVER],
      activeServerId: 'local', // Local is stored primary but hidden
      connectedServerIds: ['local', 'fleet-1'],
    };
    render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    // The component should call switchServer so the registry's activeServerId
    // migrates to the fleet server — preventing API calls from going to Local.
    expect(mockSwitchServer).toHaveBeenCalledWith('fleet-1');
  });

  it('still renders fleet server if switchServer fails (graceful degradation)', async () => {
    mockIsEmbedded = true;
    mockRegistry = {
      servers: [LOCAL_PRESET, FLEET_SERVER],
      activeServerId: 'local',
      connectedServerIds: ['local', 'fleet-1'],
    };
    mockSwitchServer.mockRejectedValueOnce(new Error('connection refused'));
    const { getByText } = render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    // switchServer was attempted (best effort)
    expect(mockSwitchServer).toHaveBeenCalledWith('fleet-1');
    // Component still shows the fleet server label — no crash on failure
    expect(getByText('Cloud')).toBeTruthy();
    // Allow the rejected promise to settle without crashing
    await Promise.resolve();
  });

  it('trigger dot is green (connected) based on effective fleet server, not hidden Local', () => {
    mockIsEmbedded = true;
    mockRegistry = {
      servers: [LOCAL_PRESET, FLEET_SERVER],
      activeServerId: 'local', // Local is still the stored primary (hidden)
      connectedServerIds: ['local', 'fleet-1'],
    };
    // Fleet server reports connected; Local (hidden) reports disconnected
    const { getClientForServer } =
      jest.requireMock<typeof import('@/stores/serverClients')>('@/stores/serverClients');
    (getClientForServer as jest.Mock).mockImplementation((id: string) =>
      id === 'fleet-1' ? { isConnected$: { get: () => true } } : null
    );
    const { container } = render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    // The trigger button dot should be green (fleet is connected),
    // not gray (which would mean Local's disconnected state leaked through).
    const triggerDot = container.querySelector('.bg-green-500');
    expect(triggerDot).not.toBeNull();
    // Reset mock
    (getClientForServer as jest.Mock).mockReturnValue(null);
  });

  it('filters localhost:5700 preset the same as 127.0.0.1:5700 in embedded mode', () => {
    mockIsEmbedded = true;
    const localhostPreset = { ...LOCAL_PRESET, baseUrl: 'http://localhost:5700' };
    mockRegistry = {
      servers: [localhostPreset],
      activeServerId: 'local',
      connectedServerIds: ['local'],
    };
    const { container } = render(<ServerSelector />);
    expect(container.firstChild).toBeNull();
  });
});

describe('ServerSelector in non-embedded mode', () => {
  beforeEach(() => {
    mockIsEmbedded = false;
    mockRegistry = {
      servers: [LOCAL_PRESET],
      activeServerId: 'local',
      connectedServerIds: ['local'],
    };
  });

  it('renders normally (shows Local server)', () => {
    const { container } = render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    expect(container.firstChild).not.toBeNull();
  });

  it('focuses and marks the server URL invalid when submitted empty', async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );

    await user.click(screen.getByText('Local'));
    await user.click(await screen.findByRole('button', { name: 'Add server' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add & Connect' }));

    const urlInput = screen.getByRole('textbox', { name: 'Server URL' });
    await waitFor(() => expect(urlInput).toHaveFocus());
    expect(urlInput).toHaveAttribute('aria-invalid', 'true');
    expect(urlInput).toHaveAccessibleDescription('Server URL is required');
  });
});
