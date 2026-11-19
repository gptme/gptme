import { useCallback, useReducer, useRef, type SetStateAction } from 'react';
import type { ChatOptions } from '@/components/ChatInput';

export interface QueuedMessage {
  id?: string;
  text: string;
  sending?: boolean;
  editDraft?: string;
  options: ChatOptions;
  uploadFailed?: boolean;
  missingFiles?: string[];
}

function readQueue(key: string): QueuedMessage[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(`${key}:queue`) || '[]');
    if (!Array.isArray(saved)) return [];
    return saved
      .filter(
        (item): item is QueuedMessage =>
          !!item &&
          typeof item.text === 'string' &&
          !!item.options &&
          typeof item.options === 'object'
      )
      .map((item) => ({
        ...item,
        id: item.id || crypto.randomUUID(),
        sending: false,
        uploadFailed: true,
      }));
  } catch {
    return [];
  }
}

/** Persist before releasing the composer; keep File objects in memory only. */
export function useMessageQueue(key: string | null) {
  const queues = useRef<Record<string, QueuedMessage[]>>({});
  const [, refresh] = useReducer((n: number) => n + 1, 0);
  if (key && !queues.current[key]) queues.current[key] = readQueue(key);

  const setQueue = useCallback(
    (update: SetStateAction<QueuedMessage[]>) => {
      if (!key) return;
      const previous = queues.current[key] ?? [];
      const next = (typeof update === 'function' ? update(previous) : update).map((item) => ({
        ...item,
        id: item.id || crypto.randomUUID(),
      }));
      const serializable = next.map(({ options, ...item }) => {
        const { pendingFiles, onFilesUploaded: _callback, ...savedOptions } = options;
        const missingFiles = pendingFiles?.length
          ? pendingFiles.map((file) => file.name)
          : item.missingFiles;
        return {
          ...item,
          options: savedOptions,
          ...(missingFiles?.length ? { missingFiles, uploadFailed: true } : {}),
        };
      });
      // Storage errors propagate to the caller: do not clear a draft that we
      // couldn't durably enqueue. Keep the previous in-memory queue on failure.
      if (serializable.length) localStorage.setItem(`${key}:queue`, JSON.stringify(serializable));
      else localStorage.removeItem(`${key}:queue`);
      queues.current[key] = next;
      refresh();
    },
    [key]
  );

  return [key ? queues.current[key] : [], setQueue] as const;
}
