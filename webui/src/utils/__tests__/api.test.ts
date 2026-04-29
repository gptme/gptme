// Mock modules that use import.meta (not available in jest)
jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  CLOUD_BASE_URL: 'https://fleet.gptme.ai',
}));
jest.mock('@/stores/conversations', () => ({}));
jest.mock('@/stores/servers', () => ({
  serverRegistry$: { get: jest.fn(() => ({ servers: [], activeServerId: null })) },
  getActiveServer: jest.fn(),
  getPrimaryClient: jest.fn(),
}));

import { isLikelyChromeCorsPna } from '../api';

describe('isLikelyChromeCorsPna', () => {
  const setHostname = (hostname: string) => {
    Object.defineProperty(window, 'location', {
      value: { ...window.location, hostname },
      writable: true,
      configurable: true,
    });
  };

  it('returns true when public origin connects to localhost', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('http://localhost:5700')).toBe(true);
  });

  it('returns true when public origin connects to 127.0.0.1', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('http://127.0.0.1:5700')).toBe(true);
  });

  it('returns true when public origin connects to private 192.168.x.x', () => {
    setHostname('example.com');
    expect(isLikelyChromeCorsPna('http://192.168.1.100:5700')).toBe(true);
  });

  it('returns false when already on localhost (no PNA concern)', () => {
    setHostname('localhost');
    expect(isLikelyChromeCorsPna('http://localhost:5700')).toBe(false);
  });

  it('returns false when public-to-public (not PNA)', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('https://api.example.com')).toBe(false);
  });

  it('returns false for invalid URL', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('not-a-url')).toBe(false);
  });
});
