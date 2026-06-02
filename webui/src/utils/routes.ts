export function chatRoute(conversationId: string, queryString?: string): string {
  const encodedId = encodeURIComponent(conversationId);
  return `/chat/${encodedId}${queryString ? `?${queryString}` : ''}`;
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
