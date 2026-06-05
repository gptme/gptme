import { parseExtensionStreamLine } from '../extensionStreamEvents';

describe('parseExtensionStreamLine', () => {
  it('reads generation_progress tokens from the current server payload', () => {
    expect(
      parseExtensionStreamLine('data: {"type":"generation_progress","token":"hello"}')
    ).toEqual({ type: 'token', token: 'hello' });
  });

  it('accepts the older nested content payload shape', () => {
    expect(
      parseExtensionStreamLine('data: {"type":"generation_progress","data":{"content":"hello"}}')
    ).toEqual({ type: 'token', token: 'hello' });
  });

  it('maps completion and error events', () => {
    expect(parseExtensionStreamLine('data: {"type":"generation_complete"}')).toEqual({
      type: 'complete',
    });
    expect(parseExtensionStreamLine('data: {"type":"error","error":"boom"}')).toEqual({
      type: 'error',
      error: 'boom',
    });
  });

  it('ignores non-data and malformed lines', () => {
    expect(parseExtensionStreamLine('event: ping')).toBeNull();
    expect(parseExtensionStreamLine('data: not-json')).toBeNull();
  });
});
