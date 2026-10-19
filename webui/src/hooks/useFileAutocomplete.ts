import { useState, useCallback, useRef } from 'react';
import { useWorkspaceApi } from '@/utils/workspaceApi';
import type { FileType } from '@/types/workspace';

interface AutocompleteState {
  isOpen: boolean;
  query: string;
  files: FileType[];
  selectedIndex: number;
  cursorPosition: number;
}

interface UseFileAutocompleteOptions {
  conversationId?: string;
  enabled?: boolean;
}

interface UseFileAutocompleteReturn {
  state: AutocompleteState;
  handleInputChange: (value: string, cursorPos: number) => void;
  handleKeyDown: (e: React.KeyboardEvent) => boolean; // Returns true if handled
  selectFile: (file: FileType) => string; // Returns new input value
  setSelectedIndex: (index: number) => void;
  close: () => void;
}

const TRIGGER_CHAR = '@';

export function useFileAutocomplete({
  conversationId,
  enabled = true,
}: UseFileAutocompleteOptions): UseFileAutocompleteReturn {
  const { listWorkspace } = useWorkspaceApi();
  const [state, setState] = useState<AutocompleteState>({
    isOpen: false,
    query: '',
    files: [],
    selectedIndex: 0,
    cursorPosition: 0,
  });

  // Cache file listings for one popup opening; cleared whenever the popup closes
  // so files created or deleted since the last opening show up on the next one.
  const fileCache = useRef<Map<string, FileType[]>>(new Map());
  const currentValue = useRef<string>('');
  // Bumped on every close so a listing request that resolves after the popup
  // closed can neither repopulate the cache nor update the popup state.
  const openingGeneration = useRef(0);

  const closePopup = useCallback((reset: Partial<AutocompleteState> = {}) => {
    openingGeneration.current += 1;
    fileCache.current.clear();
    setState((prev) => ({ ...prev, isOpen: false, ...reset }));
  }, []);

  // Flatten directory structure for autocomplete
  const flattenFiles = useCallback(
    async (basePath: string = ''): Promise<FileType[]> => {
      if (!conversationId) return [];

      const cacheKey = `${conversationId}:${basePath}`;
      if (fileCache.current.has(cacheKey)) {
        return fileCache.current.get(cacheKey)!;
      }

      const generation = openingGeneration.current;
      try {
        const files = await listWorkspace(conversationId, basePath || undefined);
        // A request that started in an earlier opening must not repopulate the
        // cache after the popup closed; the next opening needs a fresh listing.
        if (openingGeneration.current === generation) {
          fileCache.current.set(cacheKey, files);
        }
        return files;
      } catch (error) {
        console.error('[useFileAutocomplete] Error listing workspace:', error);
        return [];
      }
    },
    [conversationId, listWorkspace]
  );

  // Find @ trigger and extract query
  const findTrigger = useCallback(
    (value: string, cursorPos: number): { index: number; query: string } | null => {
      // Look backwards from cursor to find @
      for (let i = cursorPos - 1; i >= 0; i--) {
        const char = value[i];
        if (char === TRIGGER_CHAR) {
          // Check if @ is at start or preceded by whitespace
          if (i === 0 || /\s/.test(value[i - 1])) {
            const query = value.slice(i + 1, cursorPos);
            // Don't trigger if query contains spaces (means user moved past the completion)
            if (!/\s/.test(query)) {
              return { index: i, query };
            }
          }
          break;
        }
        // Stop if we hit whitespace
        if (/\s/.test(char)) break;
      }
      return null;
    },
    []
  );

  // Handle input changes
  const handleInputChange = useCallback(
    async (value: string, cursorPos: number) => {
      currentValue.current = value;

      if (!enabled || !conversationId) {
        closePopup();
        return;
      }

      const trigger = findTrigger(value, cursorPos);

      if (!trigger) {
        closePopup({ query: '', files: [] });
        return;
      }

      setState((prev) => ({
        ...prev,
        isOpen: true,
        query: trigger.query,
        cursorPosition: cursorPos,
        selectedIndex: 0,
      }));

      // Fetch files and filter by query
      const generation = openingGeneration.current;
      const files = await flattenFiles();
      const filtered = files.filter(
        (f) =>
          f.name.toLowerCase().includes(trigger.query.toLowerCase()) ||
          f.path.toLowerCase().includes(trigger.query.toLowerCase())
      );

      // Only update if this opening is still live and the value hasn't changed
      if (openingGeneration.current === generation && currentValue.current === value) {
        setState((prev) => ({
          ...prev,
          files: filtered.slice(0, 10), // Limit to 10 suggestions
        }));
      }
    },
    [enabled, conversationId, findTrigger, flattenFiles, closePopup]
  );

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent): boolean => {
      if (!state.isOpen || state.files.length === 0) return false;

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setState((prev) => ({
            ...prev,
            selectedIndex: (prev.selectedIndex + 1) % prev.files.length,
          }));
          return true;

        case 'ArrowUp':
          e.preventDefault();
          setState((prev) => ({
            ...prev,
            selectedIndex:
              prev.selectedIndex === 0 ? prev.files.length - 1 : prev.selectedIndex - 1,
          }));
          return true;

        case 'Tab':
        case 'Enter':
          if (state.files[state.selectedIndex]) {
            e.preventDefault();
            return true; // Caller should call selectFile
          }
          return false;

        case 'Escape':
          e.preventDefault();
          closePopup();
          return true;

        default:
          return false;
      }
    },
    [state.isOpen, state.files, state.selectedIndex, closePopup]
  );

  // Select a file and return new input value
  const selectFile = useCallback(
    (file: FileType): string => {
      const value = currentValue.current;
      const trigger = findTrigger(value, state.cursorPosition);

      if (!trigger) return value;

      // Replace @query with @path
      const before = value.slice(0, trigger.index);
      const after = value.slice(state.cursorPosition);
      const newValue = `${before}@${file.path}${after}`;

      closePopup({ query: '', files: [] });

      return newValue;
    },
    [findTrigger, state.cursorPosition, closePopup]
  );

  const setSelectedIndex = useCallback((index: number) => {
    setState((prev) => ({ ...prev, selectedIndex: index }));
  }, []);

  const close = useCallback(() => closePopup(), [closePopup]);

  return {
    state,
    handleInputChange,
    handleKeyDown,
    selectFile,
    setSelectedIndex,
    close,
  };
}
