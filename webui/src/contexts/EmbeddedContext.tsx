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
    if (!isEmbedded || typeof window === 'undefined' || window.parent === window) {
      return;
    }

    const referrerOrigin = getEmbeddedParentOrigin(document.referrer);
    parentOriginRef.current = referrerOrigin;
    setParentOrigin(referrerOrigin);

    const handleMessage = (event: MessageEvent) => {
      if (
        !isEmbeddedContextEventAllowed(
          event.origin,
          parentOriginRef.current,
          window.location.origin
        )
      ) {
        return;
      }

      const parsedItems = parseEmbeddedContextMessage(event.data);
      if (parsedItems) {
        setMenuItems(parsedItems);
        // Capture confirmed parent origin from first valid inbound when referrer was absent
        if (!parentOriginRef.current) {
          parentOriginRef.current = event.origin;
          setParentOrigin(event.origin);
        }
      }
    };

    window.addEventListener('message', handleMessage);
    // Ready signal carries no sensitive payload; '*' fallback is acceptable for the handshake
    window.parent.postMessage(
      { type: 'gptme-webui:embedded-context-ready' },
      referrerOrigin ?? '*'
    );

    return () => {
      window.removeEventListener('message', handleMessage);
    };
  }, [isEmbedded]);

  const sendAction = useCallback(
    (action: string, itemId?: string) => {
      if (!isEmbedded || typeof window === 'undefined' || window.parent === window) {
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
