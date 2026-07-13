import '@testing-library/jest-dom';
import { render } from '@testing-library/react';
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

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    switchServer: jest.fn(),
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
});

describe('ServerSelector in non-embedded mode', () => {
  it('renders normally (shows Local server)', () => {
    mockIsEmbedded = false;
    mockRegistry = {
      servers: [LOCAL_PRESET],
      activeServerId: 'local',
      connectedServerIds: ['local'],
    };
    const { container } = render(
      <TooltipProvider>
        <ServerSelector />
      </TooltipProvider>
    );
    expect(container.firstChild).not.toBeNull();
  });
});
