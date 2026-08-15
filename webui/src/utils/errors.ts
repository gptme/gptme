/**
 * Turn unknown thrown values into a user-visible string.
 *
 * Tauri `invoke` rejects with a plain `{ message }` object, and some API
 * bodies use `{ error: { message } }`. `String(err)` / `new Error(object)`
 * both become "[object Object]" — the desktop API-key flow was showing that
 * instead of the real sidecar/provider failure.
 */
function readMessage(value: unknown): string | null {
  if (typeof value === 'string' && value.trim() && value !== '[object Object]') {
    return value;
  }
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (typeof obj.message === 'string' && obj.message.trim() && obj.message !== '[object Object]') {
      return obj.message;
    }
    if ('error' in obj) {
      const nested = readMessage(obj.error);
      if (nested) {
        return nested;
      }
    }
  }
  return null;
}

export function formatUnknownError(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = error.message.trim();
    if (message && message !== '[object Object]') {
      return message;
    }
  }
  return readMessage(error) ?? fallback;
}

export function messageFromApiErrorBody(data: unknown, fallback: string): string {
  return readMessage(data) ?? fallback;
}
