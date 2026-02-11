/**
 * Shared client pool for all connected servers.
 *
 * Every connected server gets the same kind of ApiClient — no special-casing
 * for the "primary" server. The primary designation only controls which server
 * is the default for new conversations.
 */
import { createApiClient, type ApiClient } from '@/utils/api';
import { serverRegistry$ } from './servers';

const clientPool = new Map<string, ApiClient>();

/**
 * Get or create an ApiClient for a specific server.
 * Clients are cached and invalidated when baseUrl or auth changes.
 */
export function getClientForServer(serverId: string): ApiClient | null {
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
export function getPrimaryClient(): ApiClient {
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
