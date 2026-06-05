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
    case 'STREAM_TOKEN':
      return { ...state, stream: { ...state.stream, generating: true, buffer: state.stream.buffer + action.token } };
    case 'STREAM_DONE':
      return {
        ...state,
        msgs: state.stream.buffer
          ? [...state.msgs, { role: 'assistant' as const, content: state.stream.buffer }]
          : state.msgs,
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

  // Auto-scroll on new content
  useEffect(() => {
    msgsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.msgs, state.stream.buffer]);

  // Listen for background messages
  useEffect(() => {
    function handler(msg: Record<string, unknown>) {
      if (msg.type === 'TOKEN' && msg.convId === state.convId) {
        dispatch({ type: 'STREAM_TOKEN', token: msg.token as string });
      } else if (msg.type === 'DONE' && msg.convId === state.convId) {
        dispatch({ type: 'STREAM_DONE' });
      } else if (msg.type === 'ERROR' && msg.convId === state.convId) {
        dispatch({ type: 'STREAM_ERROR', error: String(msg.error) });
      } else if (msg.type === 'SELECTION_UPDATE') {
        dispatch({ type: 'SET_SELECTION', text: msg.selection as string | null });
      }
    }
    chrome.runtime.onMessage.addListener(handler);
    return () => chrome.runtime.onMessage.removeListener(handler);
  }, [state.convId]);

  // Connect on mount
  useEffect(() => {
    (async () => {
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
        dispatch({ type: 'DISCONNECTED', reason: `● Server error — ${String(createResp.error ?? 'failed')}` });
        return;
      }
      dispatch({ type: 'CONNECTED' });
    })();
    // Load initial selection
    sendToBg({ type: 'GET_SELECTION' }).then((data) => {
      if (data.lastSelection) {
        dispatch({ type: 'SET_SELECTION', text: data.lastSelection as string });
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const send = useCallback(async () => {
    const input = inputRef.current;
    if (!input || !state.online || state.stream.generating) return;
    const text = input.value.trim();
    if (!text) return;

    input.value = '';
    let content = text;
    if (state.selection) {
      content = `[Page context]\nSelected text:\n> ${state.selection}\n\n${text}`;
    }
    dispatch({ type: 'ADD_MESSAGE', msg: { role: 'user', content: text } });

    const resp = await sendToBg({ type: 'SEND_AND_STEP', convId: state.convId, content });
    if (!resp.ok) {
      dispatch({ type: 'STREAM_ERROR', error: String(resp.error ?? 'send failed') });
    }
  }, [state.online, state.stream.generating, state.selection, state.convId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
    },
    [send],
  );

  const clear = useCallback(() => {
    sendToBg({ type: 'CANCEL', convId: state.convId }).catch(() => {});
    dispatch({ type: 'SET_SELECTION', text: null });
    window.location.reload(); // simplest reset for the side panel
  }, [state.convId]);

  return (
    <div className="flex flex-col h-screen bg-background text-foreground">
      {/* Status bar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border text-xs shrink-0">
        <span className={state.online ? 'text-green-500' : 'text-muted-foreground'}>
          {state.status}
        </span>
        {state.selection && (
          <span className="text-muted-foreground truncate max-w-[200px]" title={state.selection}>
            📄 {state.selection.slice(0, 40)}…
          </span>
        )}
        <button
          onClick={clear}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="New conversation"
        >
          ✕
        </button>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {state.msgs.length === 0 && !state.stream.generating && (
          <p className="text-muted-foreground text-sm text-center mt-8">
            Ask gptme about anything on this page
          </p>
        )}
        {state.msgs.map((msg, i) => (
          <div key={i} className={`text-sm ${msg.role === 'user' ? 'text-right' : ''}`}>
            {msg.role !== 'user' && msg.role !== 'system' && (
              <p className="text-xs text-muted-foreground mb-0.5">gptme</p>
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
        {state.stream.generating && state.stream.buffer && (
          <div className="text-sm">
            <p className="text-xs text-muted-foreground mb-0.5">gptme</p>
            <span className="inline-block rounded-lg px-3 py-1.5 bg-muted text-foreground">
              {state.stream.buffer}
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
            placeholder={
              state.online ? 'Ask gptme…' : 'Connect to server to chat…'
            }
            disabled={!state.online || state.stream.generating}
            className="flex-1 resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm
                       placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring
                       disabled:opacity-50"
            rows={2}
          />
          <button
            onClick={() => void send()}
            disabled={!state.online || state.stream.generating}
            className="self-end rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium
                       hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
