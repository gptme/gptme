// background.ts — service worker: GptmeClient + message bus

import { parseExtensionStreamLine } from '../src/utils/extensionStreamEvents';

const DEFAULT_SERVER_URL = 'http://localhost:5700';

interface StorageSync {
  serverUrl?: string;
  apiKey?: string;
}

interface StorageLocal {
  apiKey?: string;
}

interface StorageSession {
  lastSelection?: string;
  lastSelectionUrl?: string;
  lastSelectionTitle?: string;
}

class GptmeClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey?: string
  ) {}

  private headers(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.apiKey) h['Authorization'] = `Bearer ${this.apiKey}`;
    return h;
  }

  async ping(): Promise<boolean> {
    try {
      const resp = await fetch(`${this.baseUrl}/api/v2/server/health`, {
        headers: this.headers(),
        signal: AbortSignal.timeout(3000),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  async createConversation(id: string, systemMsg?: string): Promise<void> {
    const body: Record<string, unknown> = {};
    if (systemMsg) body.system = systemMsg;
    const resp = await fetch(`${this.baseUrl}/api/v2/conversations/${id}`, {
      method: 'PUT',
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`createConversation failed: ${resp.status}`);
  }

  async postMessage(convId: string, content: string, role = 'user'): Promise<void> {
    const resp = await fetch(`${this.baseUrl}/api/v2/conversations/${convId}`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ role, content }),
    });
    if (!resp.ok) throw new Error(`postMessage failed: ${resp.status}`);
  }

  async step(convId: string): Promise<void> {
    const resp = await fetch(`${this.baseUrl}/api/v2/conversations/${convId}/step`, {
      method: 'POST',
      headers: this.headers(),
    });
    if (!resp.ok) throw new Error(`step failed: ${resp.status}`);
  }

  // Uses fetch instead of EventSource so the Authorization header can be sent
  // (EventSource does not support custom headers, which would force the API key
  // into the URL query string where it appears in server logs).
  subscribeEvents(
    convId: string,
    onToken: (text: string) => void,
    onComplete: () => void,
    onError: (msg: string) => void
  ): () => void {
    const controller = new AbortController();
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let closed = false;

    const closeStream = async () => {
      if (closed) return;
      closed = true;
      if (reader) {
        try {
          await reader.cancel();
        } catch {
          // ignore stream-close errors during teardown
        }
      }
      controller.abort();
    };

    fetch(`${this.baseUrl}/api/v2/conversations/${convId}/events`, {
      headers: { ...this.headers(), Accept: 'text/event-stream' },
      signal: controller.signal,
    })
      .then(async (resp) => {
        if (!resp.ok || !resp.body) {
          onError(`SSE request failed: ${resp.status}`);
          return;
        }
        reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            if (!closed) {
              await closeStream();
              onComplete();
            }
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            const event = parseExtensionStreamLine(line);
            if (!event) continue;

            if (event.type === 'token') {
              onToken(event.token);
              continue;
            }

            await closeStream();
            if (event.type === 'complete') {
              onComplete();
            } else {
              onError(event.error);
            }
            return;
          }
        }
      })
      .catch((err: Error) => {
        if (!closed && err.name !== 'AbortError') {
          onError('SSE connection lost');
        }
      });

    return () => {
      void closeStream();
    };
  }
}

async function getSettings(): Promise<{ serverUrl: string; apiKey?: string }> {
  const [syncData, localData] = await Promise.all([
    chrome.storage.sync.get(['serverUrl', 'apiKey']) as Promise<StorageSync>,
    chrome.storage.local.get(['apiKey']) as Promise<StorageLocal>,
  ]);

  let apiKey = localData.apiKey;
  if (!apiKey && syncData.apiKey) {
    apiKey = syncData.apiKey;
    await chrome.storage.local.set({ apiKey });
  }
  if (syncData.apiKey) {
    await chrome.storage.sync.remove('apiKey');
  }

  return {
    serverUrl: syncData.serverUrl ?? DEFAULT_SERVER_URL,
    apiKey,
  };
}

async function getClient(): Promise<GptmeClient> {
  const data = await getSettings();
  return new GptmeClient(data.serverUrl, data.apiKey);
}

interface ActiveStream {
  cancel: () => void;
  port: chrome.runtime.Port;
}

// Active SSE subscriptions keyed by conversationId
const activeStreams = new Map<string, ActiveStream>();

function stopActiveStream(convId: string) {
  activeStreams.get(convId)?.cancel();
  activeStreams.delete(convId);
}

function postToPort(port: chrome.runtime.Port, msg: Record<string, unknown>): boolean {
  try {
    port.postMessage(msg);
    return true;
  } catch {
    return false;
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'gptme-stream') return;

  let convId: string | null = null;

  port.onDisconnect.addListener(() => {
    if (convId && activeStreams.get(convId)?.port === port) {
      stopActiveStream(convId);
    }
  });

  port.onMessage.addListener((msg: Record<string, unknown>) => {
    if (msg.type === 'CANCEL') {
      if (convId) stopActiveStream(convId);
      return;
    }

    if (msg.type !== 'SEND_AND_STEP') {
      postToPort(port, { type: 'ERROR', error: `Unknown type: ${String(msg.type)}` });
      return;
    }

    void (async () => {
      convId = String(msg.convId ?? '');
      const content = typeof msg.content === 'string' ? msg.content : '';
      if (!convId || !content) {
        postToPort(port, { type: 'ERROR', convId, error: 'Missing conversation id or content' });
        return;
      }

      try {
        const cl = await getClient();
        await cl.postMessage(convId, content);

        // Cancel any existing stream, then subscribe BEFORE step so early tokens aren't missed.
        stopActiveStream(convId);

        const unsub = cl.subscribeEvents(
          convId,
          (token) => {
            if (!postToPort(port, { type: 'TOKEN', convId, token })) {
              stopActiveStream(convId);
            }
          },
          () => {
            if (activeStreams.get(convId!)?.port === port) activeStreams.delete(convId!);
            postToPort(port, { type: 'DONE', convId });
          },
          (error) => {
            if (activeStreams.get(convId!)?.port === port) activeStreams.delete(convId!);
            postToPort(port, { type: 'ERROR', convId, error });
          }
        );
        activeStreams.set(convId, { cancel: unsub, port });

        await cl.step(convId);
        postToPort(port, { type: 'STARTED', convId });
      } catch (e) {
        stopActiveStream(convId);
        postToPort(port, {
          type: 'ERROR',
          convId,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    })();
  });
});

chrome.runtime.onMessage.addListener(
  (
    msg: Record<string, unknown>,
    _sender: chrome.runtime.MessageSender,
    sendResponse: (r: unknown) => void
  ) => {
    (async () => {
      if (msg.type === 'PING') {
        const cl = await getClient();
        const ok = await cl.ping();
        sendResponse({ ok });
        return;
      }

      if (msg.type === 'CREATE_CONV') {
        try {
          const cl = await getClient();
          await cl.createConversation(msg.convId as string, msg.systemMsg as string | undefined);
          sendResponse({ ok: true });
        } catch (e) {
          sendResponse({ ok: false, error: e instanceof Error ? e.message : String(e) });
        }
        return;
      }

      if (msg.type === 'CANCEL') {
        const convId = msg.convId as string;
        stopActiveStream(convId);
        sendResponse({ ok: true });
        return;
      }

      if (msg.type === 'SELECTION') {
        // Store selection for panel retrieval on open
        await chrome.storage.session.set({
          lastSelection: msg.selection as string | undefined,
          lastSelectionUrl: msg.url as string | undefined,
          lastSelectionTitle: msg.title as string | undefined,
        } satisfies StorageSession);
        // Broadcast to open panels
        chrome.runtime
          .sendMessage({
            type: 'SELECTION_UPDATE',
            selection: msg.selection,
            url: msg.url,
            title: msg.title,
          })
          .catch(() => {});
        sendResponse({ ok: true });
        return;
      }

      if (msg.type === 'GET_SELECTION') {
        const data = (await chrome.storage.session.get([
          'lastSelection',
          'lastSelectionUrl',
          'lastSelectionTitle',
        ])) as StorageSession;
        sendResponse(data);
        return;
      }

      // Unknown message type — reply immediately so the channel closes
      sendResponse({ ok: false, error: `Unknown type: ${String(msg.type)}` });
    })();
    return true; // keep channel open for async sendResponse
  }
);

// Open side panel on action click
chrome.action.onClicked.addListener(async (tab) => {
  if (tab.id) {
    await chrome.sidePanel.open({ tabId: tab.id });
  }
});
