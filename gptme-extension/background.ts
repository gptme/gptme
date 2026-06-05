// background.ts — service worker: GptmeClient + message bus

const DEFAULT_SERVER_URL = 'http://localhost:5700';

interface StorageSync {
  serverUrl?: string;
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
    private readonly apiKey?: string,
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
    onError: (msg: string) => void,
  ): () => void {
    const controller = new AbortController();

    fetch(`${this.baseUrl}/api/v2/conversations/${convId}/events`, {
      headers: { ...this.headers(), Accept: 'text/event-stream' },
      signal: controller.signal,
    }).then(async (resp) => {
      if (!resp.ok || !resp.body) {
        onError(`SSE request failed: ${resp.status}`);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          onComplete();
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6)) as {
              type: string;
              data?: { content?: string };
            };
            if (data.type === 'generation_progress' && data.data?.content) {
              onToken(data.data.content);
            } else if (data.type === 'generation_complete') {
              onComplete();
              return;
            }
          } catch {
            // skip non-JSON lines
          }
        }
      }
    }).catch((err: Error) => {
      if (err.name !== 'AbortError') {
        onError('SSE connection lost');
      }
    });

    return () => controller.abort();
  }
}

async function getClient(): Promise<GptmeClient> {
  const data = (await chrome.storage.sync.get(['serverUrl', 'apiKey'])) as StorageSync;
  return new GptmeClient(data.serverUrl ?? DEFAULT_SERVER_URL, data.apiKey);
}

// Active SSE subscriptions keyed by conversationId
const activeStreams = new Map<string, () => void>();

chrome.runtime.onMessage.addListener(
  (
    msg: Record<string, unknown>,
    _sender: chrome.runtime.MessageSender,
    sendResponse: (r: unknown) => void,
  ) => {
    (async () => {
      if (msg.type === 'PING') {
        const cl = await getClient();
        const ok = await cl.ping();
        sendResponse({ ok });
        return;
      }

      if (msg.type === 'CREATE_CONV') {
        const cl = await getClient();
        await cl.createConversation(msg.convId as string, msg.systemMsg as string | undefined);
        sendResponse({ ok: true });
        return;
      }

      if (msg.type === 'SEND_AND_STEP') {
        const cl = await getClient();
        const convId = msg.convId as string;
        await cl.postMessage(convId, msg.content as string);
        await cl.step(convId);
        sendResponse({ ok: true });

        // Cancel any existing stream for this conversation
        activeStreams.get(convId)?.();

        const unsub = cl.subscribeEvents(
          convId,
          (token) => {
            chrome.runtime.sendMessage({ type: 'TOKEN', convId, token }).catch(() => {});
          },
          () => {
            chrome.runtime.sendMessage({ type: 'DONE', convId }).catch(() => {});
            activeStreams.delete(convId);
          },
          (error) => {
            chrome.runtime.sendMessage({ type: 'ERROR', convId, error }).catch(() => {});
            activeStreams.delete(convId);
          },
        );
        activeStreams.set(convId, unsub);
        return;
      }

      if (msg.type === 'CANCEL') {
        const convId = msg.convId as string;
        activeStreams.get(convId)?.();
        activeStreams.delete(convId);
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
        chrome.runtime.sendMessage({
          type: 'SELECTION_UPDATE',
          selection: msg.selection,
          url: msg.url,
          title: msg.title,
        }).catch(() => {});
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
  },
);

// Open side panel on action click
chrome.action.onClicked.addListener(async (tab) => {
  if (tab.id) {
    await chrome.sidePanel.open({ tabId: tab.id });
  }
});
