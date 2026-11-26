import { beforeEach, describe, expect, it } from '@jest/globals';
import {
  addServer,
  getActiveServer,
  serverRegistry$,
  sameOriginServer$,
  setActiveServer,
} from '@/stores/servers';
import { setLocation } from '@/test-utils/location';
import * as tauri from '@/utils/tauri';
import * as viteEnv from '@/utils/viteEnv';
import { getConnectionConfigFromSources } from '../connectionConfig';

// Uses the real server registry: the bug lives in how the two modules interact.
describe('getConnectionConfigFromSources #baseUrl= fragment (#5223)', () => {
  beforeEach(() => {
    serverRegistry$.set({ servers: [], activeServerId: '', connectedServerIds: [] });
    sameOriginServer$.set(null);
  });

  it('preserves selected-server token links on a custom static frontend after failed discovery', () => {
    setLocation('https://serving.example/');
    const remote = addServer({
      name: 'Remote',
      baseUrl: 'https://remote.example',
      authToken: 'remote-token',
      useAuthToken: true,
    });
    setActiveServer(remote.id);
    const config = getConnectionConfigFromSources('userToken=serving-secret');
    expect(config).toMatchObject({
      baseUrl: 'https://remote.example',
      authToken: 'serving-secret',
    });
    expect(serverRegistry$.peek().servers).toHaveLength(1);
    expect(getActiveServer()?.id).toBe(remote.id);
  });

  it.each([
    'https://chat.gptme.org/',
    'http://localhost:5173/',
    'http://127.0.0.1:4173/',
    'http://localhost:5701/',
  ])('keeps token-only links on the selected API server for static frontend %s', (origin) => {
    setLocation(origin);
    const server = addServer({
      name: 'Selected API',
      baseUrl: 'https://api.example',
      authToken: null,
      useAuthToken: false,
    });
    setActiveServer(server.id);

    expect(getConnectionConfigFromSources('userToken=api-token')).toMatchObject({
      baseUrl: 'https://api.example',
      authToken: 'api-token',
    });
    expect(serverRegistry$.peek().servers).toHaveLength(1);
    expect(getActiveServer()?.id).toBe(server.id);
  });

  it('keeps a non-loopback Vite dev client on its selected API server', () => {
    setLocation('http://dev.example:5173/');
    const dev = jest.replaceProperty(viteEnv, 'isViteDev', true);
    try {
      const server = addServer({
        name: 'Selected API',
        baseUrl: 'https://api.example',
        authToken: null,
        useAuthToken: false,
      });
      setActiveServer(server.id);
      expect(getConnectionConfigFromSources('userToken=dev-token')).toMatchObject({
        baseUrl: 'https://api.example',
        authToken: 'dev-token',
      });
    } finally {
      dev.restore();
    }
  });

  it('keeps native token-only links on the API server, not the HTTP Tauri asset origin', () => {
    setLocation('http://tauri.localhost/');
    const native = jest.spyOn(tauri, 'isTauriEnvironment').mockReturnValue(true);
    try {
      const server = addServer({
        name: 'Local',
        baseUrl: 'http://127.0.0.1:5700',
        authToken: null,
        useAuthToken: false,
      });
      setActiveServer(server.id);
      expect(getConnectionConfigFromSources('userToken=native-token')).toMatchObject({
        baseUrl: 'http://127.0.0.1:5700',
        authToken: 'native-token',
      });
    } finally {
      native.mockRestore();
    }
  });

  it('keeps the stored token of a known server when the link has no userToken', () => {
    const known = addServer({
      name: 'Known',
      baseUrl: 'https://known.example.com',
      authToken: 'stored-token',
      useAuthToken: true,
    });

    const result = getConnectionConfigFromSources('baseUrl=https://known.example.com');

    const stored = serverRegistry$.get().servers.find((s) => s.id === known.id);
    expect(stored?.authToken).toBe('stored-token');
    expect(stored?.useAuthToken).toBe(true);
    expect(result).toEqual({
      baseUrl: 'https://known.example.com',
      authToken: 'stored-token',
      useAuthToken: true,
    });
  });

  it('replaces the stored token when the link carries a userToken', () => {
    const known = addServer({
      name: 'Known',
      baseUrl: 'https://known.example.com',
      authToken: 'stored-token',
      useAuthToken: true,
    });

    getConnectionConfigFromSources('baseUrl=https://known.example.com&userToken=new-token');

    const stored = serverRegistry$.get().servers.find((s) => s.id === known.id);
    expect(stored?.authToken).toBe('new-token');
    expect(getActiveServer()?.id).toBe(known.id);
  });

  it('registers an unknown server without auth when the link has no userToken', () => {
    getConnectionConfigFromSources('baseUrl=https://new.example.com');

    const created = serverRegistry$
      .get()
      .servers.find((s) => s.baseUrl === 'https://new.example.com');
    expect(created).toBeDefined();
    expect(getActiveServer()?.id).toBe(created?.id);
    expect(created?.authToken).toBeNull();
    expect(created?.useAuthToken).toBe(false);
  });
});
