import type { Message } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { useObservable } from '@legendapp/state/react';

export const isNonUserMessage = (role?: string) => role === 'assistant' || role === 'system';

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

      const isChainStart = !previousMessage || previousMessage.role === 'user';
      const isChainEnd = !nextMessage || nextMessage.role === 'user';
      const isPartOfChain = isNonUserMessage(message.role);

      if (!isPartOfChain) return 'standalone';
      if (isChainStart && isChainEnd) return 'standalone';
      if (isChainStart) return 'start';
      if (isChainEnd) return 'end';
      return 'middle';
    } catch (error) {
      console.warn('Error calculating message chain type:', error);
      return 'standalone';
    }
  }, [message$, previousMessage$, nextMessage$]);
  return messageChainType$;
};
