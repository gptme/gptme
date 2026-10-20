import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';

const readDraft = (key: string | null) =>
  key && typeof window !== 'undefined' ? localStorage.getItem(key) || '' : '';

/**
 * The draft text plus who it belongs to.
 *
 * `key` is the persistable localStorage key for the current owner, or null
 * while the owner is unknown (unregistered or not-yet-hydrated server).
 * `owner` is the key the text came from; it survives a null key so text is
 * never handed to a different owner that becomes known later. A null owner
 * means the text was typed while no owner was known; it cannot safely be
 * attributed to any server or account that subsequently resolves.
 */
type DraftState = {
  key: string | null;
  owner: string | null;
  text: string;
};

function nextDraft(prev: DraftState, storageKey: string | null): DraftState {
  if (storageKey === null) {
    // Owner unknown: memory-only, but keep the text and its true owner.
    return { key: null, owner: prev.owner, text: prev.text };
  }
  if (prev.owner === storageKey) {
    // Same owner returning (e.g. after a registry reload): its text is ours.
    return { key: storageKey, owner: storageKey, text: prev.text };
  }
  // A different or previously unknown owner: load only its saved draft.
  // Registry hydration alone cannot prove who owned text typed without a key.
  return { key: storageKey, owner: storageKey, text: readDraft(storageKey) };
}

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
  const [draft, setDraft] = useState<DraftState>(() => ({
    key: storageKey,
    owner: storageKey,
    text: readDraft(storageKey),
  }));
  let current = draft;
  if (draft.key !== storageKey) {
    // Adjust state during render, not in an effect, so no render or effect
    // ever pairs the outgoing text with the incoming key.
    current = nextDraft(draft, storageKey);
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
        ...prev,
        text: typeof next === 'function' ? next(prev.text) : next,
      })),
    []
  );
  return [current.text, setText];
}
