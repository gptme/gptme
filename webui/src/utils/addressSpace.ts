// Helpers for scoping browser fetch behavior to local-network servers.
//
// Kept dependency-free (no `import.meta`, no stores) so it can be unit-tested
// in isolation — unlike connectionConfig.ts, which jest cannot import.

// Matches loopback and RFC1918 private (local-network) hostnames.
const PRIVATE_ADDRESS_PATTERN = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)/i;

/**
 * True when the URL targets a loopback/private (local-network) address.
 * Used to scope browser behaviors (e.g. Chrome LNA opt-in) to local servers
 * only, so connections to remote/cloud servers (gptme.ai) are unaffected.
 */
export function isLocalUrl(url: string): boolean {
  try {
    return PRIVATE_ADDRESS_PATTERN.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

/**
 * Chrome 142+ Local Network Access (LNA) blocks requests from a public origin
 * (e.g. https://chat.gptme.org) to a loopback/local address *before* CORS is
 * evaluated, unless the request opts in via `targetAddressSpace: 'local'`.
 *
 * This RequestInit field is not yet in the TS DOM lib types, so it is attached
 * via a cast. It is applied only when the target URL is local, so requests to
 * remote/cloud servers keep the default address-space behavior.
 *
 * See https://developer.chrome.com/blog/local-network-access
 */
export function withLocalAddressSpace(url: string, init: RequestInit = {}): RequestInit {
  if (!isLocalUrl(url)) return init;
  return { ...init, targetAddressSpace: 'local' } as RequestInit;
}
