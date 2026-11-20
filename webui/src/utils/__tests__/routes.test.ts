import {
  agentRoute,
  agentConversationsRoute,
  appRoute,
  chatRoute,
  decodeRouteParam,
} from '../routes';

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

describe('agent routes', () => {
  it('preserves exact path/server identity and demo mode without forwarding generation flags', () => {
    window.history.replaceState(null, '', '/agents?demo=1&step=true&server=wrong');
    const agent = { path: '/agents/Bob %2F #1', serverId: 'secondary' };
    expect(agentRoute(agent)).toBe(
      '/agents/overview?demo=1&path=%2Fagents%2FBob+%252F+%231&server=secondary'
    );
    expect(agentConversationsRoute(agent)).toBe(
      '/chat?demo=1&agentPath=%2Fagents%2FBob+%252F+%231&server=secondary'
    );
  });
});
