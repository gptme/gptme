import { chatRoute, decodeRouteParam } from '../routes';

describe('chatRoute', () => {
  it('encodes conversation IDs as one route segment', () => {
    expect(chatRoute('demo/conv-123')).toBe('/chat/demo%2Fconv-123');
    expect(chatRoute('demo/conv-123', 'server=secondary')).toBe(
      '/chat/demo%2Fconv-123?server=secondary'
    );
  });

  it('preserves the current query string by default', () => {
    window.history.replaceState(null, '', '/?demo=1');
    expect(chatRoute('demo/conv-123')).toBe('/chat/demo%2Fconv-123?demo=1');
  });

  it('allows callers to intentionally omit query params', () => {
    window.history.replaceState(null, '', '/?demo=1');
    expect(chatRoute('demo/conv-123', '')).toBe('/chat/demo%2Fconv-123');
  });
});

describe('decodeRouteParam', () => {
  it('decodes encoded route params and tolerates invalid values', () => {
    expect(decodeRouteParam('demo%2Fconv-123')).toBe('demo/conv-123');
    expect(decodeRouteParam('%')).toBe('%');
    expect(decodeRouteParam(undefined)).toBeUndefined();
  });
});
