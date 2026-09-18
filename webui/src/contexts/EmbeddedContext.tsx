import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FC,
  type PropsWithChildren,
} from 'react';
import {
  getEmbeddedParentOrigin,
  isEmbeddedContextEventAllowed,
  parseEmbeddedContextMessage,
  parseSeedPromptMessage,
  type EmbeddedMenuItem,
} from '@/lib/embeddedContext';
import { isEmbeddedMode } from '@/utils/viteEnv';

interface EmbeddedContextValue {
  isEmbedded: boolean;
  menuItems: EmbeddedMenuItem[];
  parentOrigin: string | null;
  sendAction: (action: string, itemId?: string) => void;
  /** Returns the pending seed prompt and clears it (one-shot). */
  consumeSeedPrompt: () => string | null;
}

const EmbeddedContext = createContext<EmbeddedContextValue>({
  isEmbedded: false,
  menuItems: [],
  parentOrigin: null,
  sendAction: () => {},
  consumeSeedPrompt: () => null,
});

export const EmbeddedContextProvider: FC<PropsWithChildren> = ({ children }) => {
  const isEmbedded = isEmbeddedMode;
  const [menuItems, setMenuItems] = useState<EmbeddedMenuItem[]>([]);
  const [parentOrigin, setParentOrigin] = useState<string | null>(null);
  // Ref so the message handler closure always reads the latest confirmed origin
  const parentOriginRef = useRef<string | null>(null);
  // State so consumers re-render when the seed arrives (ref alone would miss late arrivals)
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null);

  useEffect(() => {
    if (!isEmbedded || typeof window === 'undefined') {
      return;
    }

    const inIframe = window.parent !== window;
    const referrerOrigin = inIframe ? getEmbeddedParentOrigin(document.referrer) : null;
    parentOriginRef.current = referrerOrigin;
    setParentOrigin(referrerOrigin);

    const handleMessage = (event: MessageEvent) => {
      // Accept messages from parent frame (iframe case) or self (same-window case)
      if (inIframe ? event.source !== window.parent : event.source !== window) {
        return;
      }

      const seed = parseSeedPromptMessage(event.data);
      if (seed !== null) {
        // Seed prompts are injected directly into the chat input, so require a
        // confirmed same-origin or confirmed parent origin — never fall back to
        // allowing an unknown parent origin (that would let any page that can
        // reach this window inject an arbitrary prompt before the real host does).
        if (
          !isEmbeddedContextEventAllowed(
            event.origin,
            parentOriginRef.current,
            window.location.origin
          )
        ) {
          return;
        }
        if (!parentOriginRef.current) {
          parentOriginRef.current = event.origin;
          setParentOrigin(event.origin);
        }
        setSeedPrompt(seed);
        return;
      }

      if (
        !isEmbeddedContextEventAllowed(
          event.origin,
          parentOriginRef.current,
          window.location.origin,
          { allowUnknownParentOrigin: true }
        )
      ) {
        return;
      }

      if (!parentOriginRef.current) {
        parentOriginRef.current = event.origin;
        setParentOrigin(event.origin);
      }

      const parsedItems = parseEmbeddedContextMessage(event.data);
      if (!parsedItems) {
        return;
      }

      setMenuItems(parsedItems);
    };

    window.addEventListener('message', handleMessage);
    if (inIframe) {
      // Ready signal to parent; '*' fallback is acceptable for the handshake
      window.parent.postMessage(
        { type: 'gptme-webui:embedded-context-ready' },
        referrerOrigin ?? '*'
      );
    }

    return () => {
      window.removeEventListener('message', handleMessage);
    };
  }, [isEmbedded]);

  const consumeSeedPrompt = useCallback((): string | null => {
    const prompt = seedPrompt;
    setSeedPrompt(null);
    return prompt;
  }, [seedPrompt]);

  const sendAction = useCallback(
    (action: string, itemId?: string) => {
      if (!isEmbedded || typeof window === 'undefined') {
        return;
      }
      const inIframe = window.parent !== window;
      // Same-window embedding: post to self. Iframe: post to confirmed parent origin only.
      if (!inIframe) {
        window.postMessage(
          { type: 'gptme-webui:embedded-action', action, itemId },
          window.location.origin
        );
        return;
      }
      if (!parentOrigin) {
        // Don't send to '*' — skip until parent origin is confirmed via handshake
        return;
      }
      window.parent.postMessage(
        { type: 'gptme-webui:embedded-action', action, itemId },
        parentOrigin
      );
    },
    [isEmbedded, parentOrigin]
  );

  const value = useMemo(
    () => ({
      isEmbedded,
      menuItems,
      parentOrigin,
      sendAction,
      consumeSeedPrompt,
    }),
    [isEmbedded, menuItems, parentOrigin, sendAction, consumeSeedPrompt]
  );

  return <EmbeddedContext.Provider value={value}>{children}</EmbeddedContext.Provider>;
};

export const useEmbeddedContext = (): EmbeddedContextValue => useContext(EmbeddedContext);
