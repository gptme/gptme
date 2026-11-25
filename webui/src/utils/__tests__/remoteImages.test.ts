import { isRemoteImageUrl } from '../remoteImages';

describe('isRemoteImageUrl', () => {
  const origin = 'http://localhost:5701';

  it.each([
    ['relative path', '/api/v2/conversations/x/files/y.png', false],
    ['data URI', 'data:image/png;base64,AAAA', false],
    ['blob URI', 'blob:http://localhost:5701/abc', false],
    ['same-origin absolute URL', 'http://localhost:5701/api/v2/x.png', false],
    ['other host', 'https://attacker.example/p.png?d=secret', true],
    ['other port', 'http://localhost:9999/x.png', true],
    ['protocol-relative URL', '//attacker.example/x.png', true],
    ['empty string', '', false],
  ])('treats %s as remote=%s', (_name, url, expected) => {
    expect(isRemoteImageUrl(url, origin)).toBe(expected);
  });

  it('fails closed on an unparseable absolute-looking URL', () => {
    expect(isRemoteImageUrl('https://[::1', origin)).toBe(true);
  });
});
