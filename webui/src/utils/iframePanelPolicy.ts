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
 * Validate an iframe `src` before rendering. Accepted, in priority order:
 *   1. localhost / 127.0.0.1 origins (any scheme/port)
 *   2. server-relative paths (start with a single "/")
 * Everything else is rejected.
 *
 * Protocol-relative ("//host") and backslash-prefixed values are treated as
 * absolute and rejected unless they resolve to a localhost origin.
 */
export function isAllowedIframeSrc(src: string): boolean {
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
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
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
 */
export function resolveSandbox(tokens: readonly string[] | undefined): string {
  if (!tokens || tokens.length === 0) return '';
  const allowed = new Set<string>();
  for (const token of tokens) {
    if (SANDBOX_ALLOWLIST.has(token)) allowed.add(token);
  }
  return Array.from(allowed).join(' ');
}
