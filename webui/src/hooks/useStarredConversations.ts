import { useState, useCallback, useEffect } from 'react';

const STORAGE_KEY = 'gptme:starred-conversations';

function loadStarred(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return new Set<string>(parsed);
  } catch {
    // ignore parse errors
  }
  return new Set();
}

function saveStarred(ids: Set<string>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
}

export function useStarredConversations() {
  const [starred, setStarred] = useState<Set<string>>(() => loadStarred());

  // Sync across tabs
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) {
        setStarred(loadStarred());
      }
    };
    window.addEventListener('storage', handler);
    return () => window.removeEventListener('storage', handler);
  }, []);

  const toggleStar = useCallback((id: string) => {
    setStarred((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      saveStarred(next);
      return next;
    });
  }, []);

  const isStarred = useCallback((id: string) => starred.has(id), [starred]);

  return { starred, isStarred, toggleStar };
}
