import type { Message } from '@/types/conversation';

/**
 * Aggregate cost/token usage for a conversation, summed from per-message
 * metadata that gptme-server attaches to assistant messages.
 *
 * `totalTokens` is the headline input+output count (what users read as
 * "conversation size"). Cache read/creation tokens are kept separate to avoid
 * double-counting, since they are sub-categories of the provider's input.
 */
export interface ConversationCostSummary {
  totalCost: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  totalTokens: number;
  messagesWithCost: number;
  hasData: boolean;
}

export function computeConversationCost(messages: Message[]): ConversationCostSummary {
  let totalCost = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  let cacheReadTokens = 0;
  let cacheCreationTokens = 0;
  let messagesWithCost = 0;

  for (const msg of messages) {
    const meta = msg.metadata;
    if (!meta) continue;

    if (typeof meta.cost === 'number') {
      totalCost += meta.cost;
      messagesWithCost += 1;
    }

    const usage = meta.usage;
    if (usage) {
      inputTokens += usage.input_tokens ?? 0;
      outputTokens += usage.output_tokens ?? 0;
      cacheReadTokens += usage.cache_read_tokens ?? 0;
      cacheCreationTokens += usage.cache_creation_tokens ?? 0;
    }
  }

  const totalTokens = inputTokens + outputTokens;

  return {
    totalCost,
    inputTokens,
    outputTokens,
    cacheReadTokens,
    cacheCreationTokens,
    totalTokens,
    messagesWithCost,
    hasData: messagesWithCost > 0 || totalTokens > 0,
  };
}

/** Format a USD cost. Sub-cent costs keep 4 decimals so they don't render as $0.00. */
export function formatCost(cost: number): string {
  if (cost === 0) return '$0.00';
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

/** Format a token count with locale grouping (e.g. 1,234). */
export function formatTokens(tokens: number): string {
  return tokens.toLocaleString();
}
