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
