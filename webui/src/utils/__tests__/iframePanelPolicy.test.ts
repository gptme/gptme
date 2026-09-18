import {
  iframeSrcOrigin,
  isAllowedIframeSrc,
  resolvePanelSrc,
  resolveSandbox,
  sandboxHasOpaqueOrigin,
  urlOrigin,
} from '../iframePanelPolicy';

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

  it('allows the instance API origin when the caller supplies it', () => {
    const api = 'https://fleet.gptme.ai/api/v1/instances/abc';
    expect(isAllowedIframeSrc('https://fleet.gptme.ai/preview/5173/', api)).toBe(true);
    // Same origin on a different path is still the same origin.
    expect(isAllowedIframeSrc('https://fleet.gptme.ai/other/app', api)).toBe(true);
  });

  it('still rejects other origins even when an API origin is supplied', () => {
    const api = 'https://fleet.gptme.ai';
    expect(isAllowedIframeSrc('https://evil.example.com', api)).toBe(false);
    expect(isAllowedIframeSrc('https://fleet.gptme.ai.evil.com', api)).toBe(false);
    expect(isAllowedIframeSrc('http://fleet.gptme.ai', api)).toBe(false); // scheme differs
  });

  it('ignores a malformed API origin instead of opening the allowlist', () => {
    expect(isAllowedIframeSrc('https://evil.example.com', 'not a url')).toBe(false);
    expect(isAllowedIframeSrc('https://evil.example.com', '')).toBe(false);
    expect(isAllowedIframeSrc('https://evil.example.com', null)).toBe(false);
  });
});

describe('resolvePanelSrc', () => {
  const api = 'https://fleet.gptme.ai/api/v1/instances/abc';

  it('joins server-relative paths onto the API base, preserving the prefix', () => {
    expect(resolvePanelSrc('/preview/5173/', api)).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc/preview/5173/'
    );
    // Collapses doubled slashes at the join rather than emitting "//preview".
    expect(resolvePanelSrc('//preview/5173/', api)).toBe('//preview/5173/');
  });

  it('tolerates a trailing slash on the base url', () => {
    expect(resolvePanelSrc('/preview/5173/', `${api}/`)).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc/preview/5173/'
    );
  });

  it('leaves absolute and protocol-relative values unchanged', () => {
    expect(resolvePanelSrc('http://localhost:5173/', api)).toBe('http://localhost:5173/');
    expect(resolvePanelSrc('//evil.example.com/x', api)).toBe('//evil.example.com/x');
  });

  it('is a no-op without a base url', () => {
    expect(resolvePanelSrc('/preview/5173/')).toBe('/preview/5173/');
    expect(resolvePanelSrc('/preview/5173/', '')).toBe('/preview/5173/');
    expect(resolvePanelSrc('/preview/5173/', null)).toBe('/preview/5173/');
  });

  it('does not double the instance prefix for an already-prefixed src', () => {
    // Some hints carry the instance path themselves rather than being
    // instance-relative; prepending the base again would yield a URL the
    // server does not serve.
    expect(resolvePanelSrc('/api/v1/instances/abc/preview/5173/', api)).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc/preview/5173/'
    );
    // The bare instance path is also already resolved.
    expect(resolvePanelSrc('/api/v1/instances/abc', api)).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc'
    );
    // A host-qualified hint naming another instance must NOT be lifted to the
    // origin: that would load a different instance's route while still passing
    // the allowlist. It stays instance-relative instead (and simply 404s).
    expect(resolvePanelSrc('/instances/example/preview/5173/', api)).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc/instances/example/preview/5173/'
    );
    // A path that only shares a string prefix with the base path is still
    // instance-relative and must keep the prefix.
    expect(resolvePanelSrc('/api/v1/instances/abcdef/preview/1/', api)).toBe(
      'https://fleet.gptme.ai/api/v1/instances/abc/api/v1/instances/abcdef/preview/1/'
    );
  });

  it('refuses srcs that traverse out of the instance prefix', () => {
    // `URL` normalizes `..` (and `%2e%2e`), so these would otherwise escape
    // the instance prefix and load another route on the API origin.
    expect(resolvePanelSrc('/api/v1/instances/abc/../preview/5173/', api)).toBe('');
    expect(resolvePanelSrc('/api/v1/instances/abc/%2e%2e/preview/5173/', api)).toBe('');
    expect(resolvePanelSrc('/preview/../../admin', api)).toBe('');
    // `%2f` is not decoded by `URL`, so the `..` only appears after decoding.
    expect(resolvePanelSrc('/api/v1/instances/abc/..%2f..%2fadmin', api)).toBe('');
    expect(resolvePanelSrc('/api/v1/instances/abc/..%2fadmin', api)).toBe('');
  });

  it('produces a src that the allowlist then accepts', () => {
    const resolved = resolvePanelSrc('/preview/5173/', api);
    expect(isAllowedIframeSrc(resolved, urlOrigin(api))).toBe(true);
  });
});

describe('urlOrigin', () => {
  it('returns the origin of a base url', () => {
    expect(urlOrigin('https://fleet.gptme.ai/api/v1/instances/abc')).toBe('https://fleet.gptme.ai');
  });

  it('returns null for missing or malformed values', () => {
    expect(urlOrigin('')).toBe(null);
    expect(urlOrigin(undefined)).toBe(null);
    expect(urlOrigin(null)).toBe(null);
    expect(urlOrigin('/relative/path')).toBe(null);
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

describe('sandboxHasOpaqueOrigin', () => {
  it('is true for any sandbox without allow-same-origin', () => {
    // A sandboxed frame without allow-same-origin has an opaque origin,
    // serialized as "null" on postMessage.
    expect(sandboxHasOpaqueOrigin(['allow-scripts'])).toBe(true);
    expect(sandboxHasOpaqueOrigin(['allow-scripts', 'allow-same-origin'])).toBe(true);
    expect(sandboxHasOpaqueOrigin(['allow-forms'])).toBe(true);
  });

  it('is true for an empty resolved sandbox (present-but-empty attribute still sandboxes)', () => {
    // The component always renders the sandbox attribute; an empty resolved
    // value renders as `sandbox=""`, which per the HTML spec still activates
    // every restriction, including the opaque origin.
    expect(resolveSandbox([])).toBe('');
    expect(sandboxHasOpaqueOrigin([])).toBe(true);
    expect(sandboxHasOpaqueOrigin(undefined)).toBe(true);
    expect(sandboxHasOpaqueOrigin(['allow-modals'])).toBe(true);
  });

  it('is false only when allow-same-origin survives resolveSandbox', () => {
    // allow-same-origin without allow-scripts survives resolveSandbox and
    // preserves the frame origin.
    expect(sandboxHasOpaqueOrigin(['allow-same-origin'])).toBe(false);
  });
});
