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
  type EmbeddedMenuItem,
} from '@/lib/embeddedContext';

interface EmbeddedContextValue {
  isEmbedded: boolean;
  menuItems: EmbeddedMenuItem[];
  parentOrigin: string | null;
  sendAction: (action: string, itemId?: string) => void;
}

const EmbeddedContext = createContext<EmbeddedContextValue>({
  isEmbedded: false,
  menuItems: [],
  parentOrigin: null,
  sendAction: () => {},
});

export const EmbeddedContextProvider: FC<PropsWithChildren> = ({ children }) => {
  const isEmbedded = import.meta.env.VITE_EMBEDDED_MODE === 'true';
  const [menuItems, setMenuItems] = useState<EmbeddedMenuItem[]>([]);
  const [parentOrigin, setParentOrigin] = useState<string | null>(null);
  // Ref so the message handler closure always reads the latest confirmed origin
  const parentOriginRef = useRef<string | null>(null);

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
      if (event.source !== window.parent && event.source !== window) {
        return;
      }

      const parsedItems = parseEmbeddedContextMessage(event.data);
      if (!parsedItems) {
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
    }),
    [isEmbedded, menuItems, parentOrigin, sendAction]
  );

  return <EmbeddedContext.Provider value={value}>{children}</EmbeddedContext.Provider>;
};

export const useEmbeddedContext = (): EmbeddedContextValue => useContext(EmbeddedContext);
