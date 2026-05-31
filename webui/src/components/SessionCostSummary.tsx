import type { FC } from 'react';
import { use$ } from '@legendapp/state/react';
import { Coins } from 'lucide-react';
import { conversations$ } from '@/stores/conversations';
import { computeConversationCost, formatCost, formatTokens } from '@/utils/conversationCost';
import type { Message } from '@/types/conversation';

interface SessionCostSummaryProps {
  conversationId: string;
}

/**
 * Conversation-level cost/token total, summed from per-message metadata.
 *
 * Complements the per-message cost shown in ChatMessage tooltips: this answers
 * "what did this whole session cost?" at a glance, which the per-message view
 * cannot. Renders nothing when no usage data is available (older sessions,
 * demo conversations).
 */
export const SessionCostSummary: FC<SessionCostSummaryProps> = ({ conversationId }) => {
  const messages = use$(
    () => (conversations$.get(conversationId)?.data.log.get() as Message[] | undefined) ?? []
  );

  const summary = computeConversationCost(messages);
  if (!summary.hasData) return null;

  return (
    <div className="rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="mb-2 flex items-center gap-2 font-medium">
        <Coins className="h-4 w-4 text-muted-foreground" />
        <span>Session cost</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-lg font-semibold tabular-nums">{formatCost(summary.totalCost)}</span>
        <span className="text-muted-foreground">· {formatTokens(summary.totalTokens)} tokens</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <div className="flex justify-between">
          <dt>Input</dt>
          <dd className="tabular-nums">{formatTokens(summary.inputTokens)}</dd>
        </div>
        <div className="flex justify-between">
          <dt>Output</dt>
          <dd className="tabular-nums">{formatTokens(summary.outputTokens)}</dd>
        </div>
        {summary.cacheReadTokens > 0 && (
          <div className="flex justify-between">
            <dt>Cache read</dt>
            <dd className="tabular-nums">{formatTokens(summary.cacheReadTokens)}</dd>
          </div>
        )}
        {summary.cacheCreationTokens > 0 && (
          <div className="flex justify-between">
            <dt>Cache write</dt>
            <dd className="tabular-nums">{formatTokens(summary.cacheCreationTokens)}</dd>
          </div>
        )}
      </dl>
    </div>
  );
};
