import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
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

  useEffect(() => {
    if (!isEmbedded || typeof window === 'undefined' || window.parent === window) {
      return;
    }

    const resolvedParentOrigin = getEmbeddedParentOrigin(document.referrer);
    setParentOrigin(resolvedParentOrigin);

    const handleMessage = (event: MessageEvent) => {
      if (
        !isEmbeddedContextEventAllowed(event.origin, resolvedParentOrigin, window.location.origin)
      ) {
        return;
      }

      const parsedItems = parseEmbeddedContextMessage(event.data);
      if (parsedItems) {
        setMenuItems(parsedItems);
      }
    };

    window.addEventListener('message', handleMessage);
    window.parent.postMessage(
      {
        type: 'gptme-webui:embedded-context-ready',
      },
      resolvedParentOrigin ?? '*'
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

      window.parent.postMessage(
        {
          type: 'gptme-webui:embedded-action',
          action,
          itemId,
        },
        parentOrigin ?? '*'
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
