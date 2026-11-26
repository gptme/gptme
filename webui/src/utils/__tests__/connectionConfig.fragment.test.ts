import { beforeEach, describe, expect, it } from '@jest/globals';
import {
  addServer,
  getActiveServer,
  serverRegistry$,
  sameOriginServer$,
  setActiveServer,
} from '@/stores/servers';
import { setLocation } from '@/test-utils/location';
import { getConnectionConfigFromSources } from '../connectionConfig';

// Uses the real server registry: the bug lives in how the two modules interact.
describe('getConnectionConfigFromSources #baseUrl= fragment (#5223)', () => {
  beforeEach(() => {
    serverRegistry$.set({ servers: [], activeServerId: '', connectedServerIds: [] });
    sameOriginServer$.set(null);
  });

  it('never writes a token-only link to a selected remote when discovery failed', () => {
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
      baseUrl: 'https://serving.example',
      authToken: 'serving-secret',
    });
    expect(serverRegistry$.peek().servers.find((s) => s.id === remote.id)?.authToken).toBe(
      'remote-token'
    );
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
