import { describe, it, expect } from '@jest/globals';
import type { ServerRegistry } from '@/types/servers';
import {
  deriveServerName,
  getBundledLoopbackOrigin,
  migrateCloudPreset,
  migrateTauriOriginPreset,
  retargetPresetLocalToBundledOrigin,
} from '../servers';

describe('deriveServerName', () => {
  it('returns "Local" for default-port localhost URLs', () => {
    expect(deriveServerName('http://127.0.0.1')).toBe('Local');
    expect(deriveServerName('http://localhost')).toBe('Local');
    expect(deriveServerName('http://127.0.0.1:80')).toBe('Local');
    expect(deriveServerName('https://localhost:443')).toBe('Local');
  });

  it('disambiguates non-default-port localhost URLs by appending port', () => {
    // Without this, two servers added via #baseUrl= fragments both become "Local",
    // producing duplicate-named entries in the registry. See chat.gptme.org repro
    // in the bug report.
    expect(deriveServerName('http://127.0.0.1:5700')).toBe('Local:5700');
    expect(deriveServerName('http://127.0.0.1:9999')).toBe('Local:9999');
    expect(deriveServerName('http://localhost:8080')).toBe('Local:8080');
  });

  it('uses hostname for default-port remote URLs', () => {
    expect(deriveServerName('https://example.com')).toBe('example.com');
    expect(deriveServerName('http://example.com:80')).toBe('example.com');
    expect(deriveServerName('https://api.example.com:443')).toBe('api.example.com');
  });

  it('appends port for non-default-port remote URLs', () => {
    expect(deriveServerName('https://example.com:8443')).toBe('example.com:8443');
    expect(deriveServerName('http://api.example.com:8080')).toBe('api.example.com:8080');
  });

  it('returns "Server" for malformed URLs', () => {
    expect(deriveServerName('not-a-url')).toBe('Server');
    expect(deriveServerName('')).toBe('Server');
  });
});

describe('getBundledLoopbackOrigin', () => {
  it('returns the page origin for a bundled gptme-server loopback port', () => {
    expect(getBundledLoopbackOrigin('http://127.0.0.1:5799')).toBe('http://127.0.0.1:5799');
    expect(getBundledLoopbackOrigin('http://localhost:5700')).toBe('http://localhost:5700');
  });

  it('returns the page origin for default-port loopback (gptme-server on port 80)', () => {
    expect(getBundledLoopbackOrigin('http://localhost')).toBe('http://localhost');
    expect(getBundledLoopbackOrigin('http://127.0.0.1')).toBe('http://127.0.0.1');
  });

  it('ignores hosted pages and Vite/Playwright dev-server ports', () => {
    expect(getBundledLoopbackOrigin('https://chat.gptme.org')).toBeNull();
    expect(getBundledLoopbackOrigin('http://127.0.0.1:5173')).toBeNull();
    expect(getBundledLoopbackOrigin('http://127.0.0.1:4173')).toBeNull();
    expect(getBundledLoopbackOrigin('http://127.0.0.1:5701')).toBeNull();
  });

  it('ignores the Tauri webview origin, which is not a gptme-server', () => {
    // Linux/macOS desktop app; Windows uses http://tauri.localhost (non-loopback host).
    expect(getBundledLoopbackOrigin('tauri://localhost')).toBeNull();
    expect(getBundledLoopbackOrigin('http://tauri.localhost')).toBeNull();
    expect(getBundledLoopbackOrigin('file://localhost')).toBeNull();
  });
});

describe('retargetPresetLocalToBundledOrigin', () => {
  it('rewrites the stock 5700 Local preset to the bundled origin', () => {
    const registry: ServerRegistry = {
      activeServerId: 'local',
      connectedServerIds: ['local'],
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
    };

    retargetPresetLocalToBundledOrigin(registry, 'http://127.0.0.1:5799');
    expect(registry.servers[0].baseUrl).toBe('http://127.0.0.1:5799');
  });

  it('leaves a non-preset or already-matching server alone', () => {
    const registry: ServerRegistry = {
      activeServerId: 'custom',
      connectedServerIds: ['custom'],
      servers: [
        {
          id: 'custom',
          name: 'Local:5700',
          baseUrl: 'http://127.0.0.1:5700',
          authToken: null,
          useAuthToken: false,
          createdAt: 1,
          lastUsedAt: 1,
        },
      ],
    };

    retargetPresetLocalToBundledOrigin(registry, 'http://127.0.0.1:5799');
    expect(registry.servers[0].baseUrl).toBe('http://127.0.0.1:5700');
  });

  it('keeps the Local preset on the sidecar when running inside the Tauri webview', () => {
    const registry: ServerRegistry = {
      activeServerId: 'local',
      connectedServerIds: ['local'],
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
    };

    retargetPresetLocalToBundledOrigin(registry, 'tauri://localhost');
    expect(registry.servers[0].baseUrl).toBe('http://127.0.0.1:5700');
  });
});

describe('migrateCloudPreset', () => {
  it('removes the retired api.gptme.ai cloud preset by URL alone', () => {
    const registry: ServerRegistry = {
      activeServerId: 'stale',
      connectedServerIds: ['stale', 'local'],
      servers: [
        {
          id: 'stale',
          name: 'Cloud',
          baseUrl: 'https://API.GPTME.AI/',
          authToken: 'token',
          useAuthToken: true,
          createdAt: 1,
          lastUsedAt: 1,
        },
        {
          id: 'local',
          name: 'Local',
          baseUrl: 'http://127.0.0.1:5700',
          authToken: null,
          useAuthToken: false,
          createdAt: 2,
          lastUsedAt: 2,
          isPreset: true,
        },
      ],
    };

    migrateCloudPreset(registry);

    expect(registry.servers).toEqual([
      expect.objectContaining({
        id: 'local',
        baseUrl: 'http://127.0.0.1:5700',
      }),
    ]);
  });
});

describe('migrateTauriOriginPreset', () => {
  const makeServer = (over: Partial<ServerRegistry['servers'][number]>) => ({
    id: 'local',
    name: 'Local',
    baseUrl: 'http://127.0.0.1:5700',
    authToken: null,
    useAuthToken: false,
    createdAt: 1,
    lastUsedAt: 1,
    ...over,
  });
  const makeRegistry = (servers: ServerRegistry['servers']): ServerRegistry => ({
    activeServerId: 'local',
    connectedServerIds: ['local'],
    servers,
  });

  it('repairs a Local preset persisted as the Tauri asset origin by an affected release', () => {
    const registry = makeRegistry([
      makeServer({ baseUrl: 'tauri://localhost', isPreset: true, authToken: 'keep-me' }),
    ]);

    migrateTauriOriginPreset(registry);
    expect(registry.servers[0].baseUrl).toBe('http://127.0.0.1:5700');
    expect(registry.servers[0].authToken).toBe('keep-me');
  });

  it('leaves healthy presets and user-defined servers alone', () => {
    const registry = makeRegistry([
      makeServer({ baseUrl: 'http://127.0.0.1:5799', isPreset: true }),
      makeServer({ id: 'custom', baseUrl: 'tauri://localhost' }),
    ]);

    migrateTauriOriginPreset(registry);
    expect(registry.servers[0].baseUrl).toBe('http://127.0.0.1:5799');
    expect(registry.servers[1].baseUrl).toBe('tauri://localhost');
  });
});
