/**
 * Build an informative error for a failed `/api/v2/models` response.
 *
 * `response.statusText` is frequently empty over HTTP/2 and behind proxies, so
 * the previous `Failed to fetch models: ${response.statusText}` could degrade
 * to "Failed to fetch models: " with no indication of the cause (e.g. a 401
 * from an auth-protected server). This includes the status code and, when
 * available, the server-provided error body (e.g.
 * `{"error":"Missing authentication credentials"}`).
 */
export async function buildModelsFetchError(response: Response): Promise<Error> {
  let detail = response.statusText;
  try {
    const body = await response.clone().json();
    if (body && typeof body.error === 'string' && body.error) {
      detail = body.error;
    }
  } catch {
    // Body is not JSON or already consumed; fall back to statusText.
  }
  const suffix = detail ? ` ${detail}` : '';
  return new Error(`Failed to fetch models: ${response.status}${suffix}`);
}
