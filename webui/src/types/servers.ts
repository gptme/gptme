export interface ServerConfig {
  id: string;
  name: string;
  baseUrl: string;
  authToken: string | null;
  useAuthToken: boolean;
  createdAt: number;
  lastUsedAt: number;
}

export interface ServerRegistry {
  servers: ServerConfig[];
  activeServerId: string;
}

export const DEFAULT_SERVER_CONFIG: Omit<ServerConfig, 'id' | 'createdAt' | 'lastUsedAt'> = {
  name: 'Local',
  baseUrl: 'http://127.0.0.1:5700',
  authToken: null,
  useAuthToken: false,
};

export function generateServerId(): string {
  return `server_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}
