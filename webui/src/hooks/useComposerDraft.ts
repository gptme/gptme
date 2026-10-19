import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';

const readDraft = (key: string | null) =>
  key && typeof window !== 'undefined' ? localStorage.getItem(key) || '' : '';

/**
 * Unsent composer text persisted in localStorage under `storageKey` (see
 * `composerDraftKey`). A null key keeps the text in memory only.
 *
 * The text is stored together with the key it belongs to, so a key change
 * (conversation, server or account switch) shows the incoming key's draft on
 * its first render and can never write the outgoing text under the incoming
 * key. An emptied draft (e.g. after send) removes its key.
 */
export function useComposerDraft(
  storageKey: string | null
): [string, Dispatch<SetStateAction<string>>] {
  const [draft, setDraft] = useState(() => ({ key: storageKey, text: readDraft(storageKey) }));
  let current = draft;
  if (draft.key !== storageKey) {
    // Adjust state during render, not in an effect, so no render or effect
    // ever pairs the outgoing text with the incoming key.
    current = { key: storageKey, text: readDraft(storageKey) };
    setDraft(current);
  }

  useEffect(() => {
    if (!draft.key || typeof window === 'undefined') return;
    if (draft.text) {
      localStorage.setItem(draft.key, draft.text);
    } else {
      localStorage.removeItem(draft.key);
    }
  }, [draft]);

  const setText: Dispatch<SetStateAction<string>> = useCallback(
    (next) =>
      setDraft((prev) => ({
        key: prev.key,
        text: typeof next === 'function' ? next(prev.text) : next,
      })),
    []
  );
  return [current.text, setText];
}
