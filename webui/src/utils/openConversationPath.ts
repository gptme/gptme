const LOCAL_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]', '0.0.0.0']);

export function isLocalApiBaseUrl(baseUrl: string, currentOrigin?: string): boolean {
  const origin =
    currentOrigin ||
    (typeof window !== 'undefined' && window.location?.origin
      ? window.location.origin
      : 'http://localhost');

  try {
    const url = new URL(baseUrl, origin);
    return LOCAL_HOSTNAMES.has(url.hostname) || url.hostname.endsWith('.localhost');
  } catch {
    return false;
  }
}

export function buildFileUri(path: string): string {
  const slashPath = path.replace(/\\/g, '/');
  const encodedPath = slashPath
    .split('/')
    .map((segment, index) =>
      index === 0 && /^[A-Za-z]:$/.test(segment) ? segment : encodeURIComponent(segment)
    )
    .join('/');

  if (encodedPath.startsWith('/')) {
    return `file://${encodedPath}`;
  }

  return `file:///${encodedPath}`;
}
