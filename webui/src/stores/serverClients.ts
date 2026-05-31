/**
 * Shared client pool for all connected servers.
 *
 * Every connected server gets the same kind of ApiClient — no special-casing
 * for the "primary" server. The primary designation only controls which server
 * is the default for new conversations.
 */
import { createApiClient, type IApiClient } from '@/utils/api';
import { createDemoApiClient } from '@/utils/demoApiClient';
import { isDemoMode } from '@/utils/connectionConfig';
import { serverRegistry$ } from './servers';

const clientPool = new Map<string, IApiClient>();

// In demo mode every server resolves to a single shared offline client.
let demoClient: IApiClient | null = null;
function getDemoClient(): IApiClient {
  if (!demoClient) {
    demoClient = createDemoApiClient();
  }
  return demoClient;
}

/**
 * Get or create an ApiClient for a specific server.
 * Clients are cached and invalidated when baseUrl or auth changes.
 * In demo mode (`?demo=1`) every server resolves to the offline demo client.
 */
export function getClientForServer(serverId: string): IApiClient | null {
  if (isDemoMode()) {
    return getDemoClient();
  }

  const registry = serverRegistry$.get();
  const server = registry.servers.find((s) => s.id === serverId);
  if (!server) return null;

  const authHeader = server.useAuthToken && server.authToken ? `Bearer ${server.authToken}` : null;

  const existing = clientPool.get(serverId);
  if (existing && existing.baseUrl === server.baseUrl && existing.authHeader === authHeader) {
    return existing;
  }

  const client = createApiClient(server.baseUrl, authHeader);
  clientPool.set(serverId, client);
  return client;
}

/** Get the client for the primary (active) server. */
export function getPrimaryClient(): IApiClient {
  if (isDemoMode()) {
    return getDemoClient();
  }

  const registry = serverRegistry$.get();
  const client = getClientForServer(registry.activeServerId);
  if (!client) {
    // Fallback: create a client for the first server
    const fallback = registry.servers[0];
    if (fallback) {
      return getClientForServer(fallback.id)!;
    }
    throw new Error('No servers configured');
  }
  return client;
}

/** Remove cached clients for servers that are no longer connected. */
export function cleanupDisconnectedClients(): void {
  const registry = serverRegistry$.get();
  const connectedIds = new Set(registry.connectedServerIds);
  for (const [id] of clientPool) {
    if (!connectedIds.has(id)) {
      clientPool.delete(id);
    }
  }
}
