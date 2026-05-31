import { computeConversationCost, formatCost, formatTokens } from '../conversationCost';
import type { Message } from '@/types/conversation';

const msg = (overrides: Partial<Message>): Message => ({
  role: 'assistant',
  content: '',
  ...overrides,
});

describe('computeConversationCost', () => {
  it('returns empty summary for no messages', () => {
    const summary = computeConversationCost([]);
    expect(summary.hasData).toBe(false);
    expect(summary.totalCost).toBe(0);
    expect(summary.totalTokens).toBe(0);
    expect(summary.messagesWithCost).toBe(0);
  });

  it('ignores messages without metadata', () => {
    const summary = computeConversationCost([
      msg({ role: 'user', content: 'hi' }),
      msg({ content: 'no metadata' }),
    ]);
    expect(summary.hasData).toBe(false);
  });

  it('sums cost and usage across messages', () => {
    const summary = computeConversationCost([
      msg({
        metadata: {
          model: 'claude',
          cost: 0.012,
          usage: { input_tokens: 100, output_tokens: 50 },
        },
      }),
      msg({
        metadata: {
          model: 'claude',
          cost: 0.018,
          usage: {
            input_tokens: 200,
            output_tokens: 80,
            cache_read_tokens: 1000,
            cache_creation_tokens: 30,
          },
        },
      }),
    ]);
    expect(summary.totalCost).toBeCloseTo(0.03, 6);
    expect(summary.inputTokens).toBe(300);
    expect(summary.outputTokens).toBe(130);
    expect(summary.cacheReadTokens).toBe(1000);
    expect(summary.cacheCreationTokens).toBe(30);
    // total includes all processed tokens: input + output + cache (additive, not double-counted)
    expect(summary.totalTokens).toBe(1460);
    expect(summary.messagesWithCost).toBe(2);
    expect(summary.hasData).toBe(true);
  });

  it('counts token-only usage without cost as data', () => {
    const summary = computeConversationCost([
      msg({ metadata: { usage: { input_tokens: 10, output_tokens: 5 } } }),
    ]);
    expect(summary.messagesWithCost).toBe(0);
    expect(summary.totalTokens).toBe(15);
    expect(summary.hasData).toBe(true);
  });
});

describe('formatCost', () => {
  it('formats zero', () => {
    expect(formatCost(0)).toBe('$0.00');
  });
  it('keeps 4 decimals for sub-cent costs', () => {
    expect(formatCost(0.0034)).toBe('$0.0034');
  });
  it('uses 2 decimals for cent-and-up costs', () => {
    expect(formatCost(0.03)).toBe('$0.03');
    expect(formatCost(1.5)).toBe('$1.50');
  });
});

describe('formatTokens', () => {
  it('groups thousands', () => {
    expect(formatTokens(1234)).toBe('1,234');
  });
});
