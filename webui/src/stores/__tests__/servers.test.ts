import { describe, it, expect } from '@jest/globals';
import { deriveServerName } from '../servers';

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
