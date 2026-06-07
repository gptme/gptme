/**
 * ExtensionChat — chat UI for the Chrome extension side panel.
 *
 * Self-contained React component that talks to the background service worker
 * via chrome.runtime messages. Reuses the app's CSS but is independent of
 * the webui's React Query, routing, and API client infrastructure.
 *
 * Conversations are persisted through the gptme server and can be reloaded from
 * the extension history selector.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react';
import { Plus, RefreshCw } from 'lucide-react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Message {
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  hide?: boolean;
}

interface ConversationSummary {
  id: string;
  name: string;
  modified: number;
  messages: number;
  last_message_preview?: string;
}

interface ConversationResponse {
  id: string;
  name: string;
  log: Message[];
  logfile: string;
}

interface StreamState {
  generating: boolean;
  buffer: string;
}

type Action =
  | { type: 'CONNECTED' }
  | { type: 'DISCONNECTED'; reason: string }
  | { type: 'ADD_MESSAGE'; msg: Message }
  | { type: 'LOAD_CONVERSATION'; convId: string; msgs: Message[] }
  | { type: 'RESET_CONVERSATION'; convId: string }
  | { type: 'HISTORY_LOADING' }
  | { type: 'SET_HISTORY'; conversations: ConversationSummary[] }
  | { type: 'HISTORY_ERROR'; error: string }
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
  history: ConversationSummary[];
  historyLoading: boolean;
  historyError: string | null;
  selection: string | null;
}

const HISTORY_LIMIT = 20;
const SYSTEM_PROMPT = 'You are a helpful assistant accessible via a browser extension. Be concise.';

function makeConvId(): string {
  return `gptme-ext-${Date.now()}`;
}

const INIT: State = {
  msgs: [],
  stream: { generating: false, buffer: '' },
  online: false,
  status: '● Connecting…',
  convId: makeConvId(),
  history: [],
  historyLoading: false,
  historyError: null,
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
    case 'LOAD_CONVERSATION':
      return {
        ...state,
        convId: action.convId,
        msgs: action.msgs,
        stream: { generating: false, buffer: '' },
        online: true,
        status: '● Connected',
      };
    case 'RESET_CONVERSATION':
      return {
        ...state,
        convId: action.convId,
        msgs: [],
        stream: { generating: false, buffer: '' },
        online: true,
        status: '● Connected',
      };
    case 'HISTORY_LOADING':
      return { ...state, historyLoading: true, historyError: null };
    case 'SET_HISTORY':
      return {
        ...state,
        history: action.conversations,
        historyLoading: false,
        historyError: null,
      };
    case 'HISTORY_ERROR':
      return { ...state, historyLoading: false, historyError: action.error };
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

function normalizeMessages(log: Message[] | undefined): Message[] {
  return (log ?? [])
    .filter((msg) => !msg.hide)
    .map((msg) => ({
      role: msg.role,
      content: typeof msg.content === 'string' ? msg.content : String(msg.content ?? ''),
    }))
    .filter((msg) => msg.content.trim().length > 0);
}

function formatConversationLabel(conv: ConversationSummary): string {
  const name = conv.name || conv.id;
  if (!Number.isFinite(conv.modified)) return name;
  const date = new Date(conv.modified * 1000).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
  });
  return `${name} · ${date}`;
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

  const refreshHistory = useCallback(async () => {
    dispatch({ type: 'HISTORY_LOADING' });
    try {
      const resp = await sendToBg({ type: 'LIST_CONVS', limit: HISTORY_LIMIT });
      if (!resp.ok || !Array.isArray(resp.conversations)) {
        throw new Error(String(resp.error ?? 'failed to load conversations'));
      }
      dispatch({
        type: 'SET_HISTORY',
        conversations: resp.conversations as ConversationSummary[],
      });
    } catch (e) {
      dispatch({
        type: 'HISTORY_ERROR',
        error: e instanceof Error ? e.message : 'failed to load conversations',
      });
    }
  }, []);

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

        await refreshHistory();

        // Load initial selection inside the IIFE so it's guaranteed to run before
        // any user interaction that reads state.selection.
        const selData = await sendToBg({ type: 'GET_SELECTION' });
        if (selData.lastSelection) {
          dispatch({ type: 'SET_SELECTION', text: selData.lastSelection as string });
        }

        const savedConvId =
          typeof selData.lastConversationId === 'string' ? selData.lastConversationId : null;
        if (savedConvId) {
          const loadResp = await sendToBg({ type: 'LOAD_CONV', convId: savedConvId });
          if (loadResp.ok && loadResp.conversation) {
            const conversation = loadResp.conversation as ConversationResponse;
            dispatch({
              type: 'LOAD_CONVERSATION',
              convId: conversation.id || savedConvId,
              msgs: normalizeMessages(conversation.log),
            });
            return;
          }
        }

        const createResp = await sendToBg({
          type: 'CREATE_CONV',
          convId: state.convId,
          systemMsg: SYSTEM_PROMPT,
        });
        if (!createResp.ok) {
          dispatch({
            type: 'DISCONNECTED',
            reason: `● Server error — ${String(createResp.error ?? 'failed')}`,
          });
          return;
        }
        dispatch({ type: 'CONNECTED' });
      } catch (e) {
        dispatch({
          type: 'DISCONNECTED',
          reason: e instanceof Error ? e.message : '● Server offline — run `gptme server`',
        });
      }
    })();
  }, [refreshHistory]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadConversation = useCallback(
    async (convId: string) => {
      if (!state.online || state.stream.generating || convId === state.convId) return;
      streamPortRef.current?.postMessage({ type: 'CANCEL', convId: state.convId });
      streamPortRef.current?.disconnect();
      sendToBg({ type: 'CANCEL', convId: state.convId }).catch(() => {});

      const loadResp = await sendToBg({ type: 'LOAD_CONV', convId });
      if (!loadResp.ok || !loadResp.conversation) {
        dispatch({
          type: 'STREAM_ERROR',
          error: String(loadResp.error ?? 'failed to load conversation'),
        });
        return;
      }

      const conversation = loadResp.conversation as ConversationResponse;
      dispatch({
        type: 'LOAD_CONVERSATION',
        convId: conversation.id || convId,
        msgs: normalizeMessages(conversation.log),
      });
      inputRef.current?.focus();
    },
    [state.online, state.stream.generating, state.convId]
  );

  const newConversation = useCallback(async () => {
    if (!state.online || state.stream.generating) return;
    streamPortRef.current?.postMessage({ type: 'CANCEL', convId: state.convId });
    streamPortRef.current?.disconnect();
    sendToBg({ type: 'CANCEL', convId: state.convId }).catch(() => {});

    const convId = makeConvId();
    const createResp = await sendToBg({
      type: 'CREATE_CONV',
      convId,
      systemMsg: SYSTEM_PROMPT,
    });
    if (!createResp.ok) {
      dispatch({
        type: 'STREAM_ERROR',
        error: String(createResp.error ?? 'failed to create conversation'),
      });
      return;
    }

    dispatch({ type: 'RESET_CONVERSATION', convId });
    await refreshHistory();
    inputRef.current?.focus();
  }, [refreshHistory, state.online, state.stream.generating, state.convId]);

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
        void refreshHistory();
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
  }, [refreshHistory, state.online, state.stream.generating, state.selection, state.convId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
    },
    [send]
  );

  const selectedHistoryValue = state.history.some((conv) => conv.id === state.convId)
    ? state.convId
    : '';

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      {/* Status bar */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5 text-xs">
        <span className={state.online ? 'text-green-500' : 'text-muted-foreground'}>
          {state.status}
        </span>
        <div className="ml-auto flex min-w-0 items-center gap-1">
          <select
            aria-label="Conversation history"
            value={selectedHistoryValue}
            disabled={!state.online || state.stream.generating || state.historyLoading}
            onChange={(e) => {
              if (e.target.value) void loadConversation(e.target.value);
            }}
            className="h-7 max-w-[165px] truncate rounded-md border border-input bg-background px-2 text-xs
                       text-foreground disabled:opacity-50"
          >
            <option value="">{state.historyLoading ? 'Loading history' : 'History'}</option>
            {state.history.map((conv) => (
              <option key={conv.id} value={conv.id}>
                {formatConversationLabel(conv)}
              </option>
            ))}
          </select>
          <button
            onClick={() => void refreshHistory()}
            disabled={!state.online || state.stream.generating || state.historyLoading}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground
                       transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            title="Refresh history"
            aria-label="Refresh history"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => void newConversation()}
            disabled={!state.online || state.stream.generating}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground
                       transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            title="New conversation"
            aria-label="New conversation"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>
      {(state.selection || state.historyError) && (
        <div className="shrink-0 border-b border-border px-3 py-1 text-xs text-muted-foreground">
          {state.selection && (
            <span className="block truncate" title={state.selection}>
              Selection: {state.selection.slice(0, 80)}
              {state.selection.length > 80 ? '…' : ''}
            </span>
          )}
          {state.historyError && (
            <span className="block truncate text-destructive" title={state.historyError}>
              History unavailable: {state.historyError}
            </span>
          )}
        </div>
      )}

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
              className={`inline-block whitespace-pre-wrap rounded-lg px-3 py-1.5 text-left ${
                msg.role === 'user'
                  ? 'bg-primary text-primary-foreground'
                  : msg.role === 'system'
                    ? 'bg-muted text-muted-foreground'
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
