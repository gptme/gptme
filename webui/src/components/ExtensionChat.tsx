/**
 * ExtensionChat — chat UI for the Chrome extension side panel.
 *
 * Self-contained React component that talks to the background service worker
 * via chrome.runtime messages. Reuses the app's CSS but is independent of
 * the webui's React Query, routing, and API client infrastructure.
 *
 * Messages are local to this session — no persistence to the gptme server's
 * conversation log (conversations are created ephemerally).
 */

import { useCallback, useEffect, useReducer, useRef } from 'react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

interface StreamState {
  generating: boolean;
  buffer: string;
}

type Action =
  | { type: 'CONNECTED' }
  | { type: 'DISCONNECTED'; reason: string }
  | { type: 'ADD_MESSAGE'; msg: Message }
  | { type: 'STREAM_START' }
  | { type: 'STREAM_TOKEN'; token: string }
  | { type: 'STREAM_DONE' }
  | { type: 'STREAM_ERROR'; error: string }
  | { type: 'SET_SELECTION'; text: string | null };

interface State {
  msgs: Message[];
  stream: StreamState;
  online: boolean;
  status: string;
  convId: string;
  selection: string | null;
}

const INIT: State = {
  msgs: [],
  stream: { generating: false, buffer: '' },
  online: false,
  status: '● Connecting…',
  convId: `gptme-ext-${Date.now()}`,
  selection: null,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'CONNECTED':
      return { ...state, online: true, status: '● Connected' };
    case 'DISCONNECTED':
      return { ...state, online: false, status: action.reason };
    case 'ADD_MESSAGE':
      return { ...state, msgs: [...state.msgs, action.msg] };
    case 'STREAM_START':
      return { ...state, stream: { generating: true, buffer: '' } };
    case 'STREAM_TOKEN':
      return {
        ...state,
        stream: { ...state.stream, generating: true, buffer: state.stream.buffer + action.token },
      };
    case 'STREAM_DONE':
      return {
        ...state,
        msgs: [
          ...state.msgs,
          state.stream.buffer
            ? { role: 'assistant' as const, content: state.stream.buffer }
            : { role: 'system' as const, content: 'No response received.' },
        ],
        stream: { generating: false, buffer: '' },
      };
    case 'STREAM_ERROR':
      return {
        ...state,
        msgs: [...state.msgs, { role: 'system', content: `Error: ${action.error}` }],
        stream: { generating: false, buffer: '' },
      };
    case 'SET_SELECTION':
      return { ...state, selection: action.text };
    default:
      return state;
  }
}

/* ------------------------------------------------------------------ */
/*  Chrome runtime helpers                                             */
/* ------------------------------------------------------------------ */

async function sendToBg(msg: Record<string, unknown>): Promise<Record<string, unknown>> {
  return chrome.runtime.sendMessage(msg) as Promise<Record<string, unknown>>;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ExtensionChat() {
  const [state, dispatch] = useReducer(reducer, INIT);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const msgsEndRef = useRef<HTMLDivElement>(null);
  const streamPortRef = useRef<chrome.runtime.Port | null>(null);
  // Guards against double-send during the window between ADD_MESSAGE dispatch
  // and the first STREAM_TOKEN (when state.stream.generating becomes true).
  const pendingRef = useRef(false);

  // Auto-scroll on new content
  useEffect(() => {
    msgsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.msgs, state.stream.buffer]);

  // Listen for selection updates from the content script.
  useEffect(() => {
    function handler(msg: Record<string, unknown>) {
      if (msg.type === 'SELECTION_UPDATE') {
        dispatch({ type: 'SET_SELECTION', text: msg.selection as string | null });
      }
    }
    chrome.runtime.onMessage.addListener(handler);
    return () => chrome.runtime.onMessage.removeListener(handler);
  }, []);

  // Connect on mount
  useEffect(() => {
    (async () => {
      try {
        const pingResp = await sendToBg({ type: 'PING' });
        if (!pingResp.ok) {
          dispatch({ type: 'DISCONNECTED', reason: '● Server offline — run `gptme server`' });
          return;
        }
        const createResp = await sendToBg({
          type: 'CREATE_CONV',
          convId: state.convId,
          systemMsg: 'You are a helpful assistant accessible via a browser extension. Be concise.',
        });
        if (!createResp.ok) {
          dispatch({
            type: 'DISCONNECTED',
            reason: `● Server error — ${String(createResp.error ?? 'failed')}`,
          });
          return;
        }
        dispatch({ type: 'CONNECTED' });
        // Load initial selection inside the IIFE so it's guaranteed to run before
        // any user interaction that reads state.selection.
        const selData = await sendToBg({ type: 'GET_SELECTION' });
        if (selData.lastSelection) {
          dispatch({ type: 'SET_SELECTION', text: selData.lastSelection as string });
        }
      } catch (e) {
        dispatch({
          type: 'DISCONNECTED',
          reason: e instanceof Error ? e.message : '● Server offline — run `gptme server`',
        });
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const send = useCallback(() => {
    const input = inputRef.current;
    if (!input || !state.online || state.stream.generating || pendingRef.current) return;
    const text = input.value.trim();
    if (!text) return;

    pendingRef.current = true;
    input.value = '';
    let content = text;
    if (state.selection) {
      content = `[Page context]\nSelected text:\n> ${state.selection}\n\n${text}`;
    }
    dispatch({ type: 'ADD_MESSAGE', msg: { role: 'user', content: text } });
    dispatch({ type: 'STREAM_START' });

    streamPortRef.current?.disconnect();
    const port = chrome.runtime.connect({ name: 'gptme-stream' });
    streamPortRef.current = port;
    let finished = false;

    const cleanup = () => {
      if (streamPortRef.current === port) streamPortRef.current = null;
      pendingRef.current = false;
      port.onMessage.removeListener(handleMessage);
      port.onDisconnect.removeListener(handleDisconnect);
    };

    const finish = () => {
      finished = true;
      cleanup();
      try {
        port.disconnect();
      } catch {
        // ignore disconnect races during panel teardown
      }
    };

    function handleMessage(msg: Record<string, unknown>) {
      if (msg.convId && msg.convId !== state.convId) return;
      if (msg.type === 'STARTED') {
        pendingRef.current = false;
      } else if (msg.type === 'TOKEN') {
        pendingRef.current = false;
        dispatch({ type: 'STREAM_TOKEN', token: String(msg.token ?? '') });
      } else if (msg.type === 'DONE') {
        dispatch({ type: 'STREAM_DONE' });
        finish();
      } else if (msg.type === 'ERROR') {
        dispatch({ type: 'STREAM_ERROR', error: String(msg.error ?? 'send failed') });
        finish();
      }
    }

    function handleDisconnect() {
      cleanup();
      if (!finished) {
        dispatch({ type: 'STREAM_ERROR', error: 'Extension stream disconnected — retry message' });
      }
    }

    port.onMessage.addListener(handleMessage);
    port.onDisconnect.addListener(handleDisconnect);

    try {
      port.postMessage({ type: 'SEND_AND_STEP', convId: state.convId, content });
    } catch (e) {
      dispatch({ type: 'STREAM_ERROR', error: e instanceof Error ? e.message : 'send failed' });
      finish();
    }
  }, [state.online, state.stream.generating, state.selection, state.convId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
    },
    [send]
  );

  const clear = useCallback(() => {
    streamPortRef.current?.postMessage({ type: 'CANCEL', convId: state.convId });
    streamPortRef.current?.disconnect();
    sendToBg({ type: 'CANCEL', convId: state.convId }).catch(() => {});
    dispatch({ type: 'SET_SELECTION', text: null });
    window.location.reload(); // simplest reset for the side panel
  }, [state.convId]);

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      {/* Status bar */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-1.5 text-xs">
        <span className={state.online ? 'text-green-500' : 'text-muted-foreground'}>
          {state.status}
        </span>
        {state.selection && (
          <span className="max-w-[200px] truncate text-muted-foreground" title={state.selection}>
            📄 {state.selection.slice(0, 40)}…
          </span>
        )}
        <button
          onClick={clear}
          className="text-muted-foreground transition-colors hover:text-foreground"
          title="New conversation"
        >
          ✕
        </button>
      </div>

      {/* Messages area */}
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {state.msgs.length === 0 && !state.stream.generating && (
          <p className="mt-8 text-center text-sm text-muted-foreground">
            Ask gptme about anything on this page
          </p>
        )}
        {state.msgs.map((msg, i) => (
          <div key={i} className={`text-sm ${msg.role === 'user' ? 'text-right' : ''}`}>
            {msg.role !== 'user' && msg.role !== 'system' && (
              <p className="mb-0.5 text-xs text-muted-foreground">gptme</p>
            )}
            <span
              className={`inline-block rounded-lg px-3 py-1.5 ${
                msg.role === 'user'
                  ? 'bg-primary text-primary-foreground'
                  : msg.role === 'system'
                    ? 'bg-destructive/10 text-destructive'
                    : 'bg-muted text-foreground'
              }`}
            >
              {msg.content}
            </span>
          </div>
        ))}
        {state.stream.generating && (
          <div className="text-sm">
            <p className="mb-0.5 text-xs text-muted-foreground">gptme</p>
            <span className="inline-block rounded-lg bg-muted px-3 py-1.5 text-foreground">
              {state.stream.buffer || (
                <span className="text-muted-foreground">Waiting for response</span>
              )}
              <span className="animate-pulse">▌</span>
            </span>
          </div>
        )}
        <div ref={msgsEndRef} />
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-border p-3">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            onKeyDown={handleKeyDown}
            placeholder={state.online ? 'Ask gptme…' : 'Connect to server to chat…'}
            disabled={!state.online || state.stream.generating}
            className="flex-1 resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm
                       placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring
                       disabled:opacity-50"
            rows={2}
          />
          <button
            onClick={() => void send()}
            disabled={!state.online || state.stream.generating}
            className="self-end rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground
                       transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
