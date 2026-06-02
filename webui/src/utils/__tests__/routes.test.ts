import { chatRoute, decodeRouteParam } from '../routes';

describe('chatRoute', () => {
  it('encodes conversation IDs as one route segment', () => {
    expect(chatRoute('demo/conv-123')).toBe('/chat/demo%2Fconv-123');
    expect(chatRoute('demo/conv-123', 'server=secondary')).toBe(
      '/chat/demo%2Fconv-123?server=secondary'
    );
  });
});

describe('decodeRouteParam', () => {
  it('decodes encoded route params and tolerates invalid values', () => {
    expect(decodeRouteParam('demo%2Fconv-123')).toBe('demo/conv-123');
    expect(decodeRouteParam('%')).toBe('%');
    expect(decodeRouteParam(undefined)).toBeUndefined();
  });
});
