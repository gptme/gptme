export interface ServerConfig {
  id: string;
  name: string;
  baseUrl: string;
  authToken: string | null;
  useAuthToken: boolean;
  /** Short-lived instance-scoped SSE token (gptme-cloud#1076). Used for EventSource ?token= instead of the Supabase JWT. */
  sseToken?: string | null;
  createdAt: number;
  lastUsedAt: number;
  isPreset?: boolean; // pre-configured servers can't be deleted
}

export interface ServerRegistry {
  servers: ServerConfig[];
  activeServerId: string; // primary server for new conversations
  connectedServerIds: string[]; // all currently connected servers
}

export const PRESET_LOCAL: Omit<ServerConfig, 'id' | 'createdAt' | 'lastUsedAt'> = {
  name: 'Local',
  baseUrl: 'http://127.0.0.1:5700',
  authToken: null,
  useAuthToken: false,
  isPreset: true,
};

// Keep for backwards compat
export const DEFAULT_SERVER_CONFIG = PRESET_LOCAL;

export function generateServerId(): string {
  return `server_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}
