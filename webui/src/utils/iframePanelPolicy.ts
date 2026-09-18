/**
 * Source-URL allowlist and sandbox policy enforcement for iframe panels
 * (#830 Phase 3). These rules are intentionally strict: the iframe escape
 * hatch exists for local tool servers and controlled deployments, not for
 * arbitrary third-party embeds.
 */
import type { IframeSandboxToken } from '@/types/panel';

/** Sandbox tokens a descriptor may request. Anything else is dropped. */
const SANDBOX_ALLOWLIST: ReadonlySet<string> = new Set<IframeSandboxToken>([
  'allow-scripts',
  'allow-same-origin',
  'allow-forms',
  'allow-downloads',
]);

/**
 * Origin of a base URL, or null when it is missing or malformed.
 */
export function urlOrigin(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

/**
 * Resolve a panel `src`/`url` against the instance API base URL.
 *
 * Server-relative paths (``/preview/5173/``) are relative to the *instance
 * server*, not to the page rendering the panel. On deployments where the SPA
 * and the API live on different origins (gptme.ai → fleet.gptme.ai), passing
 * the relative value straight to the iframe loads it from the SPA origin and
 * 404s. Joining against ``baseUrl`` preserves any instance path prefix — a
 * ``urljoin``-style join would replace it.
 *
 * Absolute, protocol-relative, and unrecognized values pass through unchanged;
 * the allowlist then decides whether they may load.
 */
export function resolvePanelSrc(src: string, baseUrl?: string | null): string {
  if (typeof src !== 'string') return '';
  const value = src.trim();
  if (!baseUrl) return value;
  // "//host" is protocol-relative and "/\host" is a backslash variant; both are
  // absolute for policy purposes, so leave them for the allowlist to judge.
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return value;

  const base = baseUrl.trim().replace(/\/+$/, '');
  if (!base) return value;

  // Some hints already carry the instance path themselves instead of being
  // instance-relative. Prepending the base again would double the prefix and
  // produce a URL the server does not serve, so join those against the origin
  // only. This makes the resolver idempotent for already-prefixed paths while
  // keeping the documented `/preview/{port}/` convention instance-relative.
  //
  // Only the *exact* base path qualifies. A path that merely looks like an
  // instance route (`/instances/<other-id>/...`) must not be joined against
  // the origin, or a hint could load a different instance's route on our
  // origin while still passing the allowlist (cross-instance leak).
  try {
    const parsed = new URL(base);
    const basePath = parsed.pathname.replace(/\/+$/, '');
    const alreadyQualified =
      basePath !== '' && (value === basePath || value.startsWith(`${basePath}/`));
    const resolved = alreadyQualified
      ? new URL(value, parsed.origin)
      : new URL(`${base}/${value.replace(/^\/+/, '')}`);
    // `URL` normalizes `..` and percent-encoded dot segments; verify the result
    // still lives under the instance prefix. A hint like
    // `/api/v1/instances/abc/../admin` would otherwise escape it and load
    // another route on the API origin, which the allowlist accepts.
    if (
      basePath !== '' &&
      resolved.pathname !== basePath &&
      !resolved.pathname.startsWith(`${basePath}/`)
    ) {
      return '';
    }
    // Defense in depth: reject any `..` segment that only appears *after*
    // percent-decoding (`%2e%2e`, `%2f`-joined). `URL` normalizes the encoded
    // dot forms it recognizes, but a server that decodes `%2f` before routing
    // would still see a traversal. A literal `..` segment is never valid in a
    // panel src.
    let decodedPath: string;
    try {
      decodedPath = decodeURIComponent(resolved.pathname);
    } catch {
      return '';
    }
    if (decodedPath.split('/').some((segment) => segment === '..')) {
      return '';
    }
    return `${resolved.origin}${resolved.pathname}${resolved.search}`;
  } catch {
    // A non-URL base (relative path) has no origin/instance prefix to detect.
  }

  return `${base}/${value.replace(/^\/+/, '')}`;
}

/**
 * Validate an iframe `src` before rendering. Accepted, in priority order:
 *   1. localhost / 127.0.0.1 origins (any scheme/port)
 *   2. server-relative paths (start with a single "/")
 *   3. the instance API origin, when the caller supplies it
 * Everything else is rejected.
 *
 * The API origin is the deployment that serves the conversation (and therefore
 * the `/preview/{port}/` proxy). It is not a hole for arbitrary third-party
 * embeds: the caller passes exactly one origin, and it must match.
 *
 * Protocol-relative ("//host") and backslash-prefixed values are treated as
 * absolute and rejected unless they resolve to an allowed origin.
 */
export function isAllowedIframeSrc(src: string, allowedOrigin?: string | null): boolean {
  if (typeof src !== 'string' || src.trim() === '') return false;
  const value = src.trim();

  // Server-relative path: a single leading slash, not "//" (protocol-relative)
  // and not a backslash variant.
  if (value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/\\')) {
    return true;
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }

  const host = url.hostname.toLowerCase();
  if (host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1') {
    return true;
  }

  const apiOrigin = urlOrigin(allowedOrigin);
  return apiOrigin !== null && url.origin === apiOrigin;
}

/**
 * Resolve the origin an iframe `src` will load from, for strict postMessage
 * origin checks. Server-relative paths resolve against the host window origin.
 * Returns null when the origin cannot be determined.
 */
export function iframeSrcOrigin(src: string, hostOrigin?: string): string | null {
  const base = hostOrigin ?? (typeof window !== 'undefined' ? window.location.origin : undefined);
  try {
    return new URL(src, base).origin;
  } catch {
    return null;
  }
}

/**
 * Filter requested sandbox tokens down to the allowlist and return the
 * space-joined string for the iframe `sandbox` attribute. Unknown or
 * never-allowed tokens (allow-top-navigation, allow-popups, allow-modals)
 * are silently dropped. Duplicates are collapsed.
 *
 * The `allow-scripts` + `allow-same-origin` combination is unconditionally
 * forbidden: together they allow the iframe to remove its own sandbox
 * attribute and gain full access to the parent page's DOM. When both are
 * requested, `allow-same-origin` is dropped so scripts still run but cannot
 * escalate.
 */
export function resolveSandbox(tokens: readonly string[] | undefined): string {
  if (!tokens || tokens.length === 0) return '';
  const allowed = new Set<string>();
  for (const token of tokens) {
    if (SANDBOX_ALLOWLIST.has(token)) allowed.add(token);
  }
  if (allowed.has('allow-scripts') && allowed.has('allow-same-origin')) {
    allowed.delete('allow-same-origin');
  }
  return Array.from(allowed).join(' ');
}

/**
 * True when the resolved sandbox leaves the frame with an *opaque* origin,
 * i.e. the frame does not carry `allow-same-origin`.
 *
 * This mirrors exactly what the component renders: `sandbox={resolveSandbox(...)}`
 * always emits the attribute, and an empty value (`sandbox=""`) is a
 * *present* attribute — per the HTML spec it still activates every sandbox
 * restriction, including the opaque origin. So the empty resolved sandbox is
 * opaque too; only an explicit `allow-same-origin` preserves the frame origin.
 *
 * Browsers serialise an opaque origin as `"null"` on `postMessage`, so the
 * host can neither match `event.origin` against a concrete origin nor address
 * the frame with a concrete `targetOrigin`. Identity must be pinned by
 * `event.source` instead. Since `resolveSandbox` drops `allow-same-origin`
 * whenever `allow-scripts` is requested (the dangerous pairing), any sandboxed
 * panel that can script is opaque.
 */
export function sandboxHasOpaqueOrigin(tokens: readonly string[] | undefined): boolean {
  return !resolveSandbox(tokens).split(' ').includes('allow-same-origin');
}
