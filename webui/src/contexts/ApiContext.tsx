import type { ApiClient } from '@/utils/api';
import {
  getConnectionConfigFromSources,
  processConnectionFromHash,
  type ConnectionConfig,
} from '@/utils/connectionConfig';
import {
  getActiveServer,
  updateServer,
  serverRegistry$,
  setActiveServer,
  connectServer,
} from '@/stores/servers';
import { getClientForServer, getPrimaryClient } from '@/stores/serverClients';
import type { ServerConfig } from '@/types/servers';
import { type Observable, observable } from '@legendapp/state';
import { use$ } from '@legendapp/state/react';
import type { QueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { toast } from 'sonner';

interface ApiContextType {
  /** The primary server's client (shorthand for getClient(activeServerId)) */
  api: ApiClient;
  /** Get a client for any connected server. Defaults to primary. */
  getClient: (serverId?: string) => ApiClient;
  isConnecting$: Observable<boolean>;
  isConnected$: Observable<boolean>;
  isAutoConnecting$: Observable<boolean>;
  isExchangingAuthCode: boolean;
  connectionConfig: ConnectionConfig;
  updateConfig: (config: Partial<ConnectionConfig>) => void;
  connect: (config?: Partial<ConnectionConfig>) => Promise<void>;
  switchServer: (serverId: string) => Promise<void>;
  stopAutoConnect: () => void;
}

const ApiContext = createContext<ApiContextType | null>(null);

// Check if hash contains auth code (sync check)
function hasAuthCodeInHash(hash: string): boolean {
  const params = new URLSearchParams(hash);
  return Boolean(params.get('code'));
}

// Process initial hash (legacy direct token flow — registers into server registry)
const initialHash = window.location.hash.substring(1);
const needsAuthCodeExchange = hasAuthCodeInHash(initialHash);

if (!needsAuthCodeExchange && initialHash) {
  getConnectionConfigFromSources(initialHash);
}

const isConnecting$ = observable(false);
const isAutoConnecting$ = observable(false);

// Auto-connect state management
let autoConnectTimer: ReturnType<typeof setTimeout> | null = null;
let autoConnectAttempts = 0;
const MAX_AUTO_CONNECT_ATTEMPTS = 10;
const INITIAL_RETRY_DELAY = 1000;

const stopAutoConnect = () => {
  if (autoConnectTimer) {
    clearTimeout(autoConnectTimer);
    autoConnectTimer = null;
  }
  isAutoConnecting$.set(false);
  autoConnectAttempts = 0;
};

/**
 * Update the active server's connection config.
 * Used by gptme-cloud's Chat.tsx to inject Supabase session tokens.
 */
const updateConfig = (newConfig: Partial<ConnectionConfig>) => {
  const activeServer = getActiveServer();
  if (activeServer) {
    updateServer(activeServer.id, {
      ...(newConfig.baseUrl !== undefined && { baseUrl: newConfig.baseUrl }),
      ...(newConfig.authToken !== undefined && { authToken: newConfig.authToken }),
      ...(newConfig.useAuthToken !== undefined && { useAuthToken: newConfig.useAuthToken }),
    });
  }
};

export function ApiProvider({
  children,
  queryClient,
}: {
  children: ReactNode;
  queryClient: QueryClient;
}) {
  const [isExchangingAuthCode, setIsExchangingAuthCode] = useState(needsAuthCodeExchange);

  // Get client for any server from the shared pool
  const getClient = useCallback((serverId?: string): ApiClient => {
    if (serverId) {
      const client = getClientForServer(serverId);
      if (client) return client;
    }
    return getPrimaryClient();
  }, []);

  // Connect to API — tests connectivity of the active server
  const connect = useCallback(
    async (config?: Partial<ConnectionConfig>) => {
      stopAutoConnect();

      if (config) {
        // Update the active server in the registry (pool will pick up changes)
        const activeServer = getActiveServer();
        if (activeServer) {
          updateServer(activeServer.id, {
            ...(config.baseUrl !== undefined && { baseUrl: config.baseUrl }),
            ...(config.authToken !== undefined && { authToken: config.authToken }),
            ...(config.useAuthToken !== undefined && { useAuthToken: config.useAuthToken }),
          });
        }
      }

      // Get a fresh client from the pool (picks up any config changes)
      const client = getPrimaryClient();

      if (client.isConnected$.get()) {
        console.log('[ApiContext] Already connected, skipping connection');
        return;
      }

      if (isConnecting$.get()) {
        console.log('[ApiContext] Already connecting, skipping connection');
        return;
      }

      console.log('[ApiContext] Connecting to API');
      isConnecting$.set(true);
      try {
        const connected = await client.checkConnection();
        console.log('[ApiContext] Connected:', connected);
        if (!connected) {
          throw new Error('Failed to connect to API');
        }

        client.setConnected(true);

        await queryClient.invalidateQueries();
        await queryClient.refetchQueries({
          queryKey: ['conversations'],
          type: 'active',
        });

        toast.success('Connected to gptme server');
      } catch (error) {
        console.error('Failed to connect to API:', error);
        client.setConnected(false);

        let errorMessage = 'Could not connect to gptme instance.';
        if (error instanceof Error) {
          if (error.message.includes('NetworkError') || error.message.includes('CORS')) {
            errorMessage +=
              ' CORS issue detected - ensure the server has CORS enabled and is accepting requests from ' +
              window.location.origin;
          } else {
            errorMessage += ' Error: ' + error.message;
          }
        }
        toast.error(errorMessage);
        throw error;
      } finally {
        isConnecting$.set(false);
      }
    },
    [queryClient]
  );

  // Atomic server switch: changes the primary server with rollback on failure
  const switchServer = useCallback(
    async (serverId: string) => {
      const registry = serverRegistry$.get();
      const server = registry.servers.find((s: ServerConfig) => s.id === serverId);
      if (!server) throw new Error(`Server not found: ${serverId}`);

      if (serverId === registry.activeServerId) return;

      const previousActiveId = registry.activeServerId;

      // Ensure server is in connected list
      if (!registry.connectedServerIds.includes(serverId)) {
        connectServer(serverId);
      }

      setActiveServer(serverId);

      try {
        // Connect tests the new primary (pool creates/returns client for this server)
        await connect({
          baseUrl: server.baseUrl,
          authToken: server.authToken,
          useAuthToken: server.useAuthToken,
        });
      } catch (error) {
        // Rollback: restore previous active server
        setActiveServer(previousActiveId);
        throw error;
      }
    },
    [connect]
  );

  // Auto-connect with retry logic
  const autoConnect = useCallback(
    async (isInitialAttempt: boolean = false) => {
      const client = getPrimaryClient();

      if (client.isConnected$.get()) {
        console.log('[ApiContext] Already connected, stopping auto-connect');
        stopAutoConnect();
        return;
      }

      if (autoConnectAttempts >= MAX_AUTO_CONNECT_ATTEMPTS) {
        console.log('[ApiContext] Max auto-connect attempts reached, stopping');
        stopAutoConnect();
        if (!isInitialAttempt) {
          toast.error('Unable to connect to gptme server after multiple attempts');
        }
        return;
      }

      autoConnectAttempts++;
      isAutoConnecting$.set(true);

      console.log(
        `[ApiContext] Auto-connect attempt ${autoConnectAttempts}/${MAX_AUTO_CONNECT_ATTEMPTS}`
      );

      try {
        const connected = await client.checkConnection();
        if (connected) {
          console.log('[ApiContext] Auto-connect successful');
          client.setConnected(true);
          stopAutoConnect();

          await queryClient.invalidateQueries();
          await queryClient.refetchQueries({
            queryKey: ['conversations'],
            type: 'active',
          });

          if (!isInitialAttempt) {
            toast.success('Connected to gptme server');
          }
          return;
        }
      } catch (error) {
        console.log(`[ApiContext] Auto-connect attempt ${autoConnectAttempts} failed:`, error);
      }

      const delay = INITIAL_RETRY_DELAY * Math.pow(2, autoConnectAttempts - 1);
      const maxDelay = 30000;
      const nextDelay = Math.min(delay, maxDelay);

      console.log(`[ApiContext] Scheduling next auto-connect attempt in ${nextDelay}ms`);

      autoConnectTimer = setTimeout(() => {
        autoConnect(false);
      }, nextDelay);
    },
    [queryClient]
  );

  // Handle auth code exchange on mount
  useEffect(() => {
    const handleAuthCodeExchange = async () => {
      const currentHash = window.location.hash.substring(1);
      if (!hasAuthCodeInHash(currentHash)) return;

      console.log('[ApiContext] Auth code detected, starting exchange...');
      setIsExchangingAuthCode(true);

      try {
        const config = await processConnectionFromHash(currentHash);
        console.log('[ApiContext] Auth code exchange successful, connecting');
        await connect(config);
      } catch (error) {
        console.error('[ApiContext] Auth code exchange failed:', error);
        const message =
          error instanceof Error
            ? error.message
            : 'Failed to authenticate. The link may have expired.';
        toast.error(message);
      } finally {
        setIsExchangingAuthCode(false);
      }
    };

    void handleAuthCodeExchange();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Attempt initial connection (skip if auth code exchange is happening)
  useEffect(() => {
    const currentHash = window.location.hash.substring(1);
    if (hasAuthCodeInHash(currentHash)) return;

    void (async () => {
      console.log('[ApiContext] Attempting initial connection');
      await autoConnect(true);
    })();
  }, [autoConnect]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopAutoConnect();
    };
  }, []);

  // Derive connectionConfig from the active server (single source of truth)
  const registry = use$(serverRegistry$);
  const activeServer = registry.servers.find((s) => s.id === registry.activeServerId);
  const connectionConfig: ConnectionConfig = activeServer
    ? {
        baseUrl: activeServer.baseUrl,
        authToken: activeServer.authToken,
        useAuthToken: activeServer.useAuthToken,
      }
    : { baseUrl: 'http://127.0.0.1:5700', authToken: null, useAuthToken: false };

  // Primary client from pool (re-resolved each render to pick up changes)
  const api = getPrimaryClient();

  return (
    <ApiContext.Provider
      value={{
        api,
        getClient,
        isConnecting$,
        isConnected$: api.isConnected$,
        isAutoConnecting$,
        isExchangingAuthCode,
        connectionConfig,
        updateConfig,
        connect,
        switchServer,
        stopAutoConnect,
      }}
    >
      {children}
    </ApiContext.Provider>
  );
}

export function useApi() {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error('useApi must be used within an ApiProvider');
  }
  return context;
}
