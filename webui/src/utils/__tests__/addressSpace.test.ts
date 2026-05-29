import { isLocalUrl, withLocalAddressSpace } from '../addressSpace';

describe('isLocalUrl', () => {
  it.each([
    'http://localhost:5700',
    'http://127.0.0.1:5700',
    'http://[::1]:5700',
    'http://10.0.0.5:5700',
    'http://192.168.1.100:5700',
    'http://172.16.0.1:5700',
    'http://172.31.255.255:5700',
  ])('returns true for local/private address %s', (url) => {
    expect(isLocalUrl(url)).toBe(true);
  });

  it.each([
    'https://gptme.ai',
    'https://chat.gptme.org',
    'https://api.example.com:8080',
    'http://172.15.0.1:5700', // 172.15 is public (outside 172.16-31)
    'http://172.32.0.1:5700', // 172.32 is public
  ])('returns false for remote/public address %s', (url) => {
    expect(isLocalUrl(url)).toBe(false);
  });

  it('returns false for an invalid URL', () => {
    expect(isLocalUrl('not-a-url')).toBe(false);
  });
});

describe('withLocalAddressSpace', () => {
  it("adds targetAddressSpace:'loopback' for a loopback URL", () => {
    const init = withLocalAddressSpace('http://127.0.0.1:5700', { method: 'GET' });
    expect((init as RequestInit & { targetAddressSpace?: string }).targetAddressSpace).toBe(
      'loopback'
    );
    expect(init.method).toBe('GET');
  });

  it("adds targetAddressSpace:'loopback' for an IPv6 loopback URL", () => {
    const init = withLocalAddressSpace('http://[::1]:5700', { method: 'GET' });
    expect((init as RequestInit & { targetAddressSpace?: string }).targetAddressSpace).toBe(
      'loopback'
    );
  });

  it("adds targetAddressSpace:'local' for an RFC1918 private URL", () => {
    const init = withLocalAddressSpace('http://192.168.1.100:5700', { method: 'GET' });
    expect((init as RequestInit & { targetAddressSpace?: string }).targetAddressSpace).toBe(
      'local'
    );
    expect(init.method).toBe('GET');
  });

  it('does NOT add targetAddressSpace for a remote URL', () => {
    const init = withLocalAddressSpace('https://gptme.ai', { method: 'POST' });
    expect(
      (init as RequestInit & { targetAddressSpace?: string }).targetAddressSpace
    ).toBeUndefined();
    expect(init.method).toBe('POST');
  });

  it('preserves existing init fields (headers, signal)', () => {
    const controller = new AbortController();
    const headers = { Authorization: 'Bearer x' };
    const init = withLocalAddressSpace('http://localhost:5700', {
      headers,
      signal: controller.signal,
    });
    expect(init.headers).toEqual(headers);
    expect(init.signal).toBe(controller.signal);
  });

  it('does not mutate the input init object', () => {
    const original: RequestInit = { method: 'GET' };
    withLocalAddressSpace('http://localhost:5700', original);
    expect(
      (original as RequestInit & { targetAddressSpace?: string }).targetAddressSpace
    ).toBeUndefined();
  });

  it('defaults to an empty init when none is provided', () => {
    const init = withLocalAddressSpace('http://localhost:5700');
    expect((init as RequestInit & { targetAddressSpace?: string }).targetAddressSpace).toBe(
      'loopback'
    );
  });
});
