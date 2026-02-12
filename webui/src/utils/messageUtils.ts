import type { Message } from '@/types/conversation';
import { type Observable } from '@legendapp/state';
import { useObservable } from '@legendapp/state/react';

export const isNonUserMessage = (role?: string) => role === 'assistant' || role === 'system';

/** Check if two roles belong to the same chain group (assistant/system chain together, user chains with user) */
const isSameChainGroup = (roleA?: string, roleB?: string): boolean => {
  if (!roleA || !roleB) return false;
  if (roleA === roleB) return true;
  // assistant and system chain together
  return isNonUserMessage(roleA) && isNonUserMessage(roleB);
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

      const chainedWithPrev = isSameChainGroup(message.role, previousMessage?.role);
      const chainedWithNext = isSameChainGroup(message.role, nextMessage?.role);

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
