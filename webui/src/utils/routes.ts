export function chatRoute(conversationId: string, queryString?: string): string {
  const encodedId = encodeURIComponent(conversationId);
  const search = queryString ?? currentSearchParams();
  return `/chat/${encodedId}${search ? `?${search}` : ''}`;
}

function currentSearchParams(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.location.search.replace(/^\?/, '');
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
