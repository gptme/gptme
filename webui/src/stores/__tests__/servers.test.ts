import { describe, it, expect } from '@jest/globals';
import type { ServerRegistry } from '@/types/servers';
import {
  deriveServerName,
  probeSameOriginServer,
  adoptSameOriginServer,
  getBundledLoopbackOrigin,
  migrateCloudPreset,
  migrateTauriOriginPreset,
  retargetPresetLocalToBundledOrigin,
} from '../servers';

describe('same-origin discovery', () => {
  const mockFetch = jest.fn();
  beforeEach(() => mockFetch.mockReset());

  it('recognizes a non-loopback gptme API without sending stored credentials', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ api_version: 2, message: 'gptme v2 API' }),
    });
    expect(await probeSameOriginServer('http://myhost:5700', mockFetch)).toBe('http://myhost:5700');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://myhost:5700/api/v2',
      expect.objectContaining({
        redirect: 'error',
        credentials: 'same-origin',
      })
    );
  });

  it.each([
    {
      ok: true,
      json: async () => {
        throw new SyntaxError('index.html');
      },
    },
    { ok: true, json: async () => ({ api_version: 2, message: 'another API' }) },
    { ok: true, json: async () => null },
    { ok: false, json: async () => ({ api_version: 2, message: 'gptme v2 API' }) },
  ])('rejects non-gptme responses', async (response) => {
    mockFetch.mockResolvedValue(response);
    expect(await probeSameOriginServer('https://chat.gptme.org', mockFetch)).toBeNull();
  });

  it('falls back on network failure', async () => {
    mockFetch.mockRejectedValue(new TypeError('offline'));
    expect(await probeSameOriginServer('https://example.com', mockFetch)).toBeNull();
  });

  it.each(['tauri://localhost', 'file://localhost', 'null', ''])(
    'never fetches a non-HTTP origin %s',
    async (origin) => {
      expect(await probeSameOriginServer(origin, mockFetch)).toBeNull();
      expect(mockFetch).not.toHaveBeenCalled();
    }
  );

  const makeRegistry = (): ServerRegistry => ({
    activeServerId: 'local',
    connectedServerIds: ['local'],
    servers: [
      {
        id: 'local',
        name: 'Local',
        baseUrl: 'http://127.0.0.1:5700',
        authToken: 'loopback-secret',
        useAuthToken: true,
        sseToken: 'loopback-sse',
        isPreset: true,
        createdAt: 1,
        lastUsedAt: 1,
      },
    ],
  });

  it('replaces Local with the serving host without leaking a different server token', () => {
    const registry = makeRegistry();
    adoptSameOriginServer(registry, 'http://myhost:5700');
    expect(registry.servers).toHaveLength(1);
    expect(registry.servers[0]).toMatchObject({
      name: 'myhost:5700',
      baseUrl: 'http://myhost:5700',
      authToken: null,
      useAuthToken: false,
      sseToken: null,
    });
    expect(registry.activeServerId).toBe('local');
  });

  it('reuses a registered same-origin server and its token instead of creating a duplicate', () => {
    const registry = makeRegistry();
    registry.servers.push({
      ...registry.servers[0],
      id: 'lan',
      isPreset: false,
      baseUrl: 'http://myhost:5700',
      authToken: 'lan-token',
    });
    adoptSameOriginServer(registry, 'http://myhost:5700');
    expect(registry.servers).toHaveLength(1);
    expect(registry.servers[0]).toMatchObject({
      id: 'lan',
      authToken: 'lan-token',
      isPreset: false,
    });
    expect(registry.activeServerId).toBe('lan');
    expect(registry.connectedServerIds).toEqual(['lan']);
  });

  it('keeps a stored same-origin token and supports repeated discovery', () => {
    const registry = makeRegistry();
    registry.servers[0].baseUrl = 'http://myhost:5700';
    adoptSameOriginServer(registry, 'http://myhost:5700');
    adoptSameOriginServer(registry, 'http://myhost:5700');
    expect(registry.servers[0].authToken).toBe('loopback-secret');
    expect(registry.servers[0].name).toBe('myhost:5700');
  });

  it('preserves a legacy remote URL marked as a preset', () => {
    const registry = makeRegistry();
    registry.servers[0].baseUrl = 'https://legacy.example.com';
    adoptSameOriginServer(registry, 'http://myhost:5700');
    expect(registry.servers[0]).toMatchObject({
      baseUrl: 'https://legacy.example.com',
      authToken: 'loopback-secret',
    });
    expect(registry.activeServerId).toBe('local');
    expect(registry.servers).toContainEqual(
      expect.objectContaining({ baseUrl: 'http://myhost:5700', authToken: null })
    );
  });

  it('keeps an existing serving server disconnected when another server is selected', () => {
    const registry = makeRegistry();
    registry.servers[0].baseUrl = 'http://myhost:5700';
    registry.servers.push({
      ...registry.servers[0],
      id: 'custom',
      isPreset: false,
      baseUrl: 'https://other.example.com',
    });
    registry.activeServerId = 'custom';
    registry.connectedServerIds = ['custom'];
    adoptSameOriginServer(registry, 'http://myhost:5700');
    expect(registry.activeServerId).toBe('custom');
    expect(registry.connectedServerIds).toEqual(['custom']);
  });

  it('does not override an explicitly selected remote server', () => {
    const registry = makeRegistry();
    registry.servers.push({
      ...registry.servers[0],
      id: 'custom',
      isPreset: false,
      baseUrl: 'https://other.example.com',
    });
    registry.activeServerId = 'custom';
    adoptSameOriginServer(registry, 'http://myhost:5700');
    expect(registry.activeServerId).toBe('custom');
    expect(registry.servers[1].baseUrl).toBe('https://other.example.com');
  });
});

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
