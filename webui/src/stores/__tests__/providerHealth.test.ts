import { describe, expect, it } from '@jest/globals';
import { allProvidersDown } from '../providerHealth';

describe('allProvidersDown', () => {
  it('returns false when provider health data is absent', () => {
    expect(allProvidersDown(null)).toBe(false);
  });

  it('returns false for an empty or legacy response shape', () => {
    expect(allProvidersDown({ providers: {} })).toBe(false);
    expect(allProvidersDown({} as never)).toBe(false);
  });

  it('returns true only when every reported provider is down', () => {
    expect(
      allProvidersDown({
        providers: {
          anthropic: { status: 'error', latency_ms: null, error: 'unavailable' },
          openai: { status: 'error', latency_ms: null, error: 'unavailable' },
        },
      })
    ).toBe(true);

    expect(
      allProvidersDown({
        providers: {
          anthropic: { status: 'error', latency_ms: null, error: 'unavailable' },
          openai: { status: 'configured', latency_ms: null, error: null },
        },
      })
    ).toBe(false);
  });
});
