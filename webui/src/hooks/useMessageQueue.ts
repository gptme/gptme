import { useCallback, useSyncExternalStore, type SetStateAction } from 'react';
import type { ChatOptions } from '@/components/ChatInput';

export interface QueuedMessage {
  id?: string;
  text: string;
  sending?: boolean;
  editDraft?: string;
  options: ChatOptions;
  uploadFailed?: boolean;
  /** The send may have been saved server-side; never repost without an explicit check. */
  saveUnknown?: boolean;
  missingFiles?: string[];
}

const queues = new Map<string, QueuedMessage[]>();
const listeners = new Set<() => void>();
const empty: QueuedMessage[] = [];
const newId = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;

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
        id: item.id || newId(),
        sending: false,
        uploadFailed: true,
        // A send interrupted by reload has the same ambiguous outcome as a lost response.
        ...(item.sending || item.saveUnknown ? { saveUnknown: true } : {}),
      }));
  } catch {
    return [];
  }
}

/** Persist before releasing the composer; keep File objects in memory only. */
export function useMessageQueue(key: string | null) {
  const snapshot = useCallback(() => {
    if (!key) return empty;
    if (!queues.has(key)) queues.set(key, readQueue(key));
    return queues.get(key)!;
  }, [key]);
  const subscribe = useCallback((listener: () => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) queues.clear();
    };
  }, []);
  const items = useSyncExternalStore(subscribe, snapshot, snapshot);

  const setQueue = useCallback(
    (update: SetStateAction<QueuedMessage[]>, opts?: { memoryFallback?: boolean }) => {
      if (!key) return false;
      // The cache is cleared when the last composer unmounts; a pending send's
      // captured setter must then start from storage, not an empty queue.
      const previous = queues.get(key) ?? readQueue(key);
      const next = (typeof update === 'function' ? update(previous) : update).map((item) => ({
        ...item,
        id: item.id || newId(),
      }));
      const serializable = next.map(({ options, ...item }) => {
        const {
          pendingFiles,
          onFilesUploaded: _callback,
          onPersistenceOutcome: _outcome,
          ...savedOptions
        } = options;
        const missingFiles = pendingFiles?.length
          ? pendingFiles.map((file) => file.name)
          : item.missingFiles;
        return {
          ...item,
          options: savedOptions,
          ...(missingFiles?.length ? { missingFiles, uploadFailed: true } : {}),
        };
      });
      try {
        if (serializable.length) localStorage.setItem(`${key}:queue`, JSON.stringify(serializable));
        else localStorage.removeItem(`${key}:queue`);
      } catch {
        // Settling a finished send must still unlock the UI; storage keeps the
        // stale row (restored as unconfirmed) until the next successful write.
        if (!opts?.memoryFallback) return false;
        queues.set(key, next);
        listeners.forEach((listener) => listener());
        return false;
      }
      queues.set(key, next);
      listeners.forEach((listener) => listener());
      return true;
    },
    [key]
  );

  return [items, setQueue] as const;
}
