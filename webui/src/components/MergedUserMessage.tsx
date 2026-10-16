import type { ComponentProps, FC } from 'react';
import type { Observable } from '@legendapp/state';
import { useObservable } from '@legendapp/state/react';
import type { Message } from '@/types/conversation';
import { ChatMessage } from './ChatMessage';

type Props = Omit<ComponentProps<typeof ChatMessage>, 'message$'> & {
  /** The run's user messages, in order */
  messages$: Observable<Message>[];
  /** Changes when the run does (e.g. another prompt joins it) */
  runKey: string;
};

const FENCE_RE = /^\s*(`{3,}|~{3,})(\S*)/;

/**
 * Close code fences a message leaves open, so they can't swallow the message
 * after it. Follows gptme's convention: a fence with a language opens (and
 * can nest), a bare fence closes.
 */
export const closeOpenFences = (content: string): string => {
  const open: string[] = [];
  for (const line of content.split('\n')) {
    const match = FENCE_RE.exec(line);
    if (!match) continue;
    const [, fence, lang] = match;
    if (lang || open.length === 0) open.push(fence);
    else open.pop();
  }
  return open.length ? `${content}\n${open.reverse().join('\n')}` : content;
};

/**
 * Consecutive user messages shown as one: their text in order, separated by a
 * blank line, with all their files. Each message stays separate in the log.
 */
export const MergedUserMessage: FC<Props> = ({ messages$, runKey, ...props }) => {
  const merged$ = useObservable<Message>(() => {
    const messages = messages$.map((message$) => message$.get());
    const last = messages[messages.length - 1];
    return {
      ...last,
      content: messages.map((message) => closeOpenFences(message.content)).join('\n\n'),
      files: messages.flatMap((message) => message.files ?? []),
    };
  }, [runKey]);
  return <ChatMessage message$={merged$} {...props} />;
};
