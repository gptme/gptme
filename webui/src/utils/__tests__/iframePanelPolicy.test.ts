import { iframeSrcOrigin, isAllowedIframeSrc, resolveSandbox } from '../iframePanelPolicy';

describe('isAllowedIframeSrc', () => {
  it('allows localhost and 127.0.0.1 origins on any port', () => {
    expect(isAllowedIframeSrc('http://localhost:8080')).toBe(true);
    expect(isAllowedIframeSrc('http://localhost:6080/vnc.html')).toBe(true);
    expect(isAllowedIframeSrc('https://127.0.0.1:3000/app')).toBe(true);
    expect(isAllowedIframeSrc('http://[::1]:9000')).toBe(true);
  });

  it('allows server-relative paths', () => {
    expect(isAllowedIframeSrc('/preview/app')).toBe(true);
    expect(isAllowedIframeSrc('/api/v2/conversations/x/ui')).toBe(true);
  });

  it('rejects protocol-relative and backslash-prefixed values', () => {
    expect(isAllowedIframeSrc('//evil.example.com')).toBe(false);
    expect(isAllowedIframeSrc('/\\evil.example.com')).toBe(false);
  });

  it('rejects arbitrary external origins', () => {
    expect(isAllowedIframeSrc('https://evil.example.com')).toBe(false);
    expect(isAllowedIframeSrc('http://localhost.evil.com')).toBe(false);
    expect(isAllowedIframeSrc('https://notlocalhost')).toBe(false);
  });

  it('rejects empty, whitespace, and malformed values', () => {
    expect(isAllowedIframeSrc('')).toBe(false);
    expect(isAllowedIframeSrc('   ')).toBe(false);
    expect(isAllowedIframeSrc('not a url')).toBe(false);
    // @ts-expect-error guarding non-string at runtime
    expect(isAllowedIframeSrc(undefined)).toBe(false);
  });
});

describe('iframeSrcOrigin', () => {
  it('returns the origin for absolute localhost urls', () => {
    expect(iframeSrcOrigin('http://localhost:8080/app')).toBe('http://localhost:8080');
  });

  it('resolves server-relative paths against the host origin', () => {
    expect(iframeSrcOrigin('/preview', 'https://chat.gptme.org')).toBe('https://chat.gptme.org');
  });

  it('falls back to the window origin when no host origin is given', () => {
    // jsdom provides window.location.origin === 'http://localhost'
    expect(iframeSrcOrigin('/preview')).toBe('http://localhost');
  });

  it('returns null for malformed values', () => {
    expect(iframeSrcOrigin('http://', 'http://localhost')).toBe(null);
  });
});

describe('resolveSandbox', () => {
  it('keeps allowlisted tokens', () => {
    expect(resolveSandbox(['allow-scripts'])).toBe('allow-scripts');
    expect(resolveSandbox(['allow-same-origin'])).toBe('allow-same-origin');
    expect(resolveSandbox(['allow-forms', 'allow-downloads'])).toBe('allow-forms allow-downloads');
  });

  it('drops allow-same-origin when allow-scripts is also present (sandbox escape guard)', () => {
    // Both together let an iframe remove its own sandbox attribute.
    expect(resolveSandbox(['allow-scripts', 'allow-same-origin'])).toBe('allow-scripts');
    expect(resolveSandbox(['allow-same-origin', 'allow-scripts'])).toBe('allow-scripts');
  });

  it('drops never-allowed tokens', () => {
    expect(resolveSandbox(['allow-scripts', 'allow-top-navigation', 'allow-popups'])).toBe(
      'allow-scripts'
    );
    expect(resolveSandbox(['allow-modals'])).toBe('');
  });

  it('collapses duplicates and handles empty input', () => {
    expect(resolveSandbox(['allow-scripts', 'allow-scripts'])).toBe('allow-scripts');
    expect(resolveSandbox([])).toBe('');
    expect(resolveSandbox(undefined)).toBe('');
  });
});
