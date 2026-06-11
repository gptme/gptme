import { useState, useCallback, useEffect, useRef } from 'react';

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
  const skipNextSave = useRef(false);

  // Persist to localStorage, but skip when the change originated from another tab
  // to avoid a cross-tab echo loop (tab B updates state → saves → triggers storage
  // event in tab A → tab A updates state → saves → …).
  useEffect(() => {
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    saveStarred(starred);
  }, [starred]);

  // Sync across tabs
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) {
        skipNextSave.current = true;
        setStarred(loadStarred());
      }
    };
    window.addEventListener('storage', handler);
    return () => window.removeEventListener('storage', handler);
  }, []);

  // Sync to localStorage whenever starred changes
  useEffect(() => {
    saveStarred(starred);
  }, [starred]);

  const toggleStar = useCallback((id: string) => {
    setStarred((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const isStarred = useCallback((id: string) => starred.has(id), [starred]);

  return { starred, isStarred, toggleStar };
}
