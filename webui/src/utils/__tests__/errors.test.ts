import { formatUnknownError, messageFromApiErrorBody } from '../errors';

describe('formatUnknownError', () => {
  it('prefers Error.message', () => {
    expect(formatUnknownError(new Error('permission denied'), 'fallback')).toBe('permission denied');
  });

  it('accepts string errors', () => {
    expect(formatUnknownError('sidecar exited', 'fallback')).toBe('sidecar exited');
  });

  it('extracts Tauri invoke object message instead of [object Object]', () => {
    expect(
      formatUnknownError({ message: 'Sidecar error: Failed to execute script' }, 'fallback')
    ).toBe('Sidecar error: Failed to execute script');
  });

  it('extracts nested API error objects', () => {
    expect(
      formatUnknownError({ error: { message: 'Cannot retrieve provider settings' } }, 'fallback')
    ).toBe('Cannot retrieve provider settings');
  });

  it('uses fallback for empty or unknown values', () => {
    expect(formatUnknownError(undefined, 'fallback')).toBe('fallback');
    expect(formatUnknownError(new Error(''), 'fallback')).toBe('fallback');
    expect(formatUnknownError({}, 'fallback')).toBe('fallback');
  });
});

describe('messageFromApiErrorBody', () => {
  it('reads a string error field', () => {
    expect(messageFromApiErrorBody({ error: 'invalid key' }, 'fallback')).toBe('invalid key');
  });

  it('reads a nested error.message object used by some providers', () => {
    expect(
      messageFromApiErrorBody({ error: { message: 'Cannot retrieve provider settings' } }, 'fallback')
    ).toBe('Cannot retrieve provider settings');
  });
});
