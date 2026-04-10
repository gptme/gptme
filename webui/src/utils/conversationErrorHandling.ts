import type { Message } from '@/types/conversation';

export function findLatestAssistantIndexForError(log?: Pick<Message, 'role'>[]): number {
  if (!log?.length) {
    return -1;
  }

  const lastIndex = log.length - 1;
  if (log[lastIndex]?.role === 'assistant') {
    return lastIndex;
  }

  if (log[lastIndex]?.role === 'system' && log[lastIndex - 1]?.role === 'assistant') {
    return lastIndex - 1;
  }

  return -1;
}
