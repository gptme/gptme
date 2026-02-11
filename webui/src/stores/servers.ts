import { observable } from '@legendapp/state';
import {
  DEFAULT_SERVER_CONFIG,
  PRESET_CLOUD,
  generateServerId,
  type ServerConfig,
  type ServerRegistry,
} from '@/types/servers';

const STORAGE_KEY = 'gptme_servers';

// Legacy localStorage keys (pre-multi-backend)
const LEGACY_BASE_URL_KEY = 'gptme_baseUrl';
const LEGACY_USER_TOKEN_KEY = 'gptme_userToken';

function loadRegistry(): ServerRegistry {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as ServerRegistry;
      if (parsed.servers?.length > 0) {
        // Ensure connectedServerIds exists (migration from phase 1 format)
        if (!parsed.connectedServerIds) {
          parsed.connectedServerIds = [parsed.activeServerId];
        }
        // Ensure Cloud preset exists
        ensurePresets(parsed);
        return parsed;
      }
    }
  } catch {
    // Corrupted or unavailable storage
  }

  // Migrate from legacy keys
  return migrateFromLegacy();
}

/** Ensure both Local and Cloud presets exist in the registry */
function ensurePresets(registry: ServerRegistry): void {
  const normalized = (url: string) => url.toLowerCase().replace(/\/+$/, '');
  const hasCloud = registry.servers.some(
    (s) => normalized(s.baseUrl) === normalized(PRESET_CLOUD.baseUrl)
  );
  if (!hasCloud) {
    registry.servers.push({
      ...PRESET_CLOUD,
      id: generateServerId(),
      createdAt: Date.now(),
      lastUsedAt: 0,
    });
  }
}

function migrateFromLegacy(): ServerRegistry {
  let baseUrl = DEFAULT_SERVER_CONFIG.baseUrl;
  let authToken: string | null = null;
  let useAuthToken = false;

  try {
    const legacyUrl = localStorage.getItem(LEGACY_BASE_URL_KEY);
    const legacyToken = localStorage.getItem(LEGACY_USER_TOKEN_KEY);

    if (legacyUrl) baseUrl = legacyUrl;
    if (legacyToken) {
      authToken = legacyToken;
      useAuthToken = true;
    }
  } catch {
    // localStorage unavailable
  }

  const localServer: ServerConfig = {
    id: generateServerId(),
    name: 'Local',
    baseUrl,
    authToken,
    useAuthToken,
    isPreset: true,
    createdAt: Date.now(),
    lastUsedAt: Date.now(),
  };

  const cloudServer: ServerConfig = {
    ...PRESET_CLOUD,
    id: generateServerId(),
    createdAt: Date.now(),
    lastUsedAt: 0,
  };

  const registry: ServerRegistry = {
    servers: [localServer, cloudServer],
    activeServerId: localServer.id,
    connectedServerIds: [localServer.id],
  };

  persistRegistry(registry);
  return registry;
}

function persistRegistry(registry: ServerRegistry) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(registry));
  } catch {
    console.warn('[ServerStore] localStorage unavailable, config will not persist');
  }
}

/** Normalize URL for duplicate detection: lowercase, strip trailing slash */
function normalizeUrl(url: string): string {
  return url.toLowerCase().replace(/\/+$/, '');
}

// Initialize the observable
export const serverRegistry$ = observable<ServerRegistry>(loadRegistry());

// Persist on every change
serverRegistry$.onChange(({ value }) => {
  persistRegistry(value);
});

export function getActiveServer(): ServerConfig | undefined {
  const registry = serverRegistry$.get();
  return registry.servers.find((s) => s.id === registry.activeServerId);
}

export function setActiveServer(id: string): void {
  const registry = serverRegistry$.get();
  const server = registry.servers.find((s) => s.id === id);
  if (!server) return;

  serverRegistry$.activeServerId.set(id);
  // Update lastUsedAt
  const idx = registry.servers.indexOf(server);
  serverRegistry$.servers[idx].lastUsedAt.set(Date.now());
}

export function getConnectedServers(): ServerConfig[] {
  const registry = serverRegistry$.get();
  return registry.servers.filter((s) => registry.connectedServerIds.includes(s.id));
}

export function isServerConnected(id: string): boolean {
  return serverRegistry$.connectedServerIds.get().includes(id);
}

export function connectServer(id: string): void {
  const registry = serverRegistry$.get();
  if (!registry.connectedServerIds.includes(id)) {
    serverRegistry$.connectedServerIds.push(id);
  }
}

export function disconnectServer(id: string): void {
  const registry = serverRegistry$.get();
  const idx = registry.connectedServerIds.indexOf(id);
  if (idx !== -1) {
    serverRegistry$.connectedServerIds.splice(idx, 1);
  }
  // If we disconnected the active server, switch to first remaining connected
  if (registry.activeServerId === id) {
    const remaining = serverRegistry$.connectedServerIds.get();
    if (remaining.length > 0) {
      serverRegistry$.activeServerId.set(remaining[0]);
    }
  }
}

export function addServer(
  config: Omit<ServerConfig, 'id' | 'createdAt' | 'lastUsedAt'>
): ServerConfig {
  const registry = serverRegistry$.get();

  // Check for duplicate URL
  const normalized = normalizeUrl(config.baseUrl);
  const duplicate = registry.servers.find((s) => normalizeUrl(s.baseUrl) === normalized);
  if (duplicate) {
    throw new Error(`A server with URL "${config.baseUrl}" already exists: "${duplicate.name}"`);
  }

  const server: ServerConfig = {
    ...config,
    id: generateServerId(),
    createdAt: Date.now(),
    lastUsedAt: Date.now(),
  };

  serverRegistry$.servers.push(server);
  return server;
}

export function updateServer(
  id: string,
  updates: Partial<Omit<ServerConfig, 'id' | 'createdAt'>>
): void {
  const registry = serverRegistry$.get();
  const idx = registry.servers.findIndex((s) => s.id === id);
  if (idx === -1) return;

  // Check for duplicate URL if URL is being changed
  if (updates.baseUrl) {
    const normalized = normalizeUrl(updates.baseUrl);
    const duplicate = registry.servers.find(
      (s) => s.id !== id && normalizeUrl(s.baseUrl) === normalized
    );
    if (duplicate) {
      throw new Error(`A server with URL "${updates.baseUrl}" already exists: "${duplicate.name}"`);
    }
  }

  const current = registry.servers[idx];
  serverRegistry$.servers[idx].set({ ...current, ...updates });
}

export function removeServer(id: string): void {
  const registry = serverRegistry$.get();
  const server = registry.servers.find((s) => s.id === id);

  if (server?.isPreset) {
    throw new Error('Cannot remove a pre-configured server');
  }

  if (registry.servers.length <= 1) {
    throw new Error('Cannot remove the last server');
  }

  const idx = registry.servers.findIndex((s) => s.id === id);
  if (idx === -1) return;

  serverRegistry$.servers.splice(idx, 1);

  // Remove from connected list
  const connIdx = registry.connectedServerIds.indexOf(id);
  if (connIdx !== -1) {
    serverRegistry$.connectedServerIds.splice(connIdx, 1);
  }

  // If we removed the active server, switch to the first remaining one
  if (registry.activeServerId === id) {
    const remaining = serverRegistry$.servers.get();
    if (remaining.length > 0) {
      serverRegistry$.activeServerId.set(remaining[0].id);
    }
  }
}

/**
 * Find or create a server by URL. Used when registering servers from URL fragments
 * or auth code exchange. Returns the server (existing or newly created).
 */
export function findOrCreateServerByUrl(
  baseUrl: string,
  defaults?: Partial<Omit<ServerConfig, 'id' | 'createdAt' | 'lastUsedAt' | 'baseUrl'>>
): ServerConfig {
  const registry = serverRegistry$.get();
  const normalized = normalizeUrl(baseUrl);
  const existing = registry.servers.find((s) => normalizeUrl(s.baseUrl) === normalized);

  if (existing) {
    // Update auth if provided
    if (defaults?.authToken !== undefined) {
      updateServer(existing.id, {
        authToken: defaults.authToken,
        useAuthToken: defaults.useAuthToken ?? Boolean(defaults.authToken),
      });
    }
    return serverRegistry$.get().servers.find((s) => s.id === existing.id)!;
  }

  // Derive a name from the URL
  const name = defaults?.name || deriveServerName(baseUrl);
  return addServer({
    name,
    baseUrl,
    authToken: defaults?.authToken ?? null,
    useAuthToken: defaults?.useAuthToken ?? false,
  });
}

function deriveServerName(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost') {
      return 'Local';
    }
    return parsed.hostname;
  } catch {
    return 'Server';
  }
}
