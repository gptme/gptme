export function appRoute(path: string): string {
  const search = currentSearchParams();
  return `${path}${search ? `?${search}` : ''}`;
}

export function chatRoute(conversationId: string, queryString?: string): string {
  const encodedId = encodeURIComponent(conversationId);
  const search = queryString ?? currentSearchParams();
  return `/chat/${encodedId}${search ? `?${search}` : ''}`;
}

// Only forward params that are safe to carry across navigation.
// ?step=true is ephemeral (cleaned up by MainLayout's replace); forwarding it
// would trigger auto-generation on the wrong conversation if the user navigates
// before the cleanup fires.
const FORWARDED_PARAMS = new Set(['demo']);

function currentSearchParams(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  const params = new URLSearchParams(window.location.search);
  const forwarded = new URLSearchParams();
  for (const [key, value] of params) {
    if (FORWARDED_PARAMS.has(key)) {
      forwarded.set(key, value);
    }
  }
  return forwarded.toString();
}

export function decodeRouteParam(value: string | undefined): string | undefined {
  if (!value) {
    return value;
  }

  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
