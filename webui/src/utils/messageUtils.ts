import type { Message } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { useObservable } from '@legendapp/state/react';

export const isNonUserMessage = (role?: string) => role === 'assistant' || role === 'system';

/**
 * Check if the current message chains with an adjacent message.
 * Same-role always chains. System also chains with a preceding assistant
 * (tool output is part of the assistant turn), but assistant never chains
 * with a preceding system (it should start fresh with its own avatar).
 */
const chainsWithPrev = (current?: string, prev?: string): boolean => {
  if (!current || !prev) return false;
  if (current === prev) return true;
  // system continues an assistant chain, but not vice-versa
  return current === 'system' && prev === 'assistant';
};

export const useMessageChainType = (
  message$: Observable<Message>,
  previousMessage$: Observable<Message | undefined> | undefined,
  nextMessage$: Observable<Message | undefined> | undefined
) => {
  const messageChainType$ = useObservable(() => {
    try {
      const message = message$.get();
      if (!message) return 'standalone';

      const previousMessage = previousMessage$?.get();
      const nextMessage = nextMessage$?.get();

      const chainedWithPrev = chainsWithPrev(message.role, previousMessage?.role);
      const chainedWithNext = chainsWithPrev(nextMessage?.role, message.role);

      if (!chainedWithPrev && !chainedWithNext) return 'standalone';
      if (!chainedWithPrev && chainedWithNext) return 'start';
      if (chainedWithPrev && !chainedWithNext) return 'end';
      return 'middle';
    } catch (error) {
      console.warn('Error calculating message chain type:', error);
      return 'standalone';
    }
  }, [message$, previousMessage$, nextMessage$]);
  return messageChainType$;
};

/**
 * Group consecutive user messages among the visible rows, to render each run
 * as one message (several prompts sent in a row, e.g. queued while the agent
 * was working, read as one). Returns the rows with each run's later messages
 * removed, and each run's indices keyed by its first.
 */
export const mergeUserRuns = (
  visible: number[],
  roleAt: (index: number) => string | undefined
): { rows: number[]; runs: Map<number, number[]> } => {
  const rows: number[] = [];
  const runs = new Map<number, number[]>();
  let run: number[] | null = null;
  for (const index of visible) {
    if (roleAt(index) !== 'user') {
      run = null;
      rows.push(index);
    } else if (run) {
      run.push(index);
    } else {
      run = [index];
      runs.set(index, run);
      rows.push(index);
    }
  }
  for (const [head, indices] of runs) {
    if (indices.length < 2) runs.delete(head);
  }
  return { rows, runs };
};
