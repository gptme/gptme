import { appRoute, chatRoute, decodeRouteParam } from '../routes';

describe('chatRoute', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

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

  it('does not forward ephemeral params like step=true', () => {
    window.history.replaceState(null, '', '/?step=true');
    expect(chatRoute('conv-123')).toBe('/chat/conv-123');
  });

  it('forwards demo but strips step when both are present', () => {
    window.history.replaceState(null, '', '/?demo=1&step=true');
    expect(chatRoute('demo/conv-123')).toBe('/chat/demo%2Fconv-123?demo=1');
  });
});

describe('appRoute', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('preserves durable mode params across top-level navigation', () => {
    window.history.replaceState(null, '', '/chat/demo?demo=1&step=true');
    expect(appRoute('/agents')).toBe('/agents?demo=1');
  });
});

describe('decodeRouteParam', () => {
  it('decodes encoded route params and tolerates invalid values', () => {
    expect(decodeRouteParam('demo%2Fconv-123')).toBe('demo/conv-123');
    expect(decodeRouteParam('%')).toBe('%');
    expect(decodeRouteParam(undefined)).toBeUndefined();
  });
});
