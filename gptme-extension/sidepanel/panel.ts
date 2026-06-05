// panel.ts — side panel chat UI
export {};

interface State {
  convId: string;
  serverOnline: boolean;
  generating: boolean;
  currentAssistantText: string;
  selection: string | null;
}

const state: State = {
  convId: `gptme-ext-${Date.now()}`,
  serverOnline: false,
  generating: false,
  currentAssistantText: '',
  selection: null,
};

function el<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

// --- DOM helpers ---

function appendMessage(
  role: 'user' | 'assistant' | 'system',
  content: string,
): HTMLElement {
  const messages = el('messages');
  const div = document.createElement('div');
  div.className = `message ${role}`;

  if (role !== 'system') {
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = role === 'user' ? 'You' : 'gptme';
    div.appendChild(label);
  }

  const text = document.createElement('div');
  text.className = 'text';
  text.textContent = content;
  div.appendChild(text);

  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function setStatus(msg: string, cls: 'online' | 'offline' | 'checking'): void {
  const s = el('status');
  s.textContent = msg;
  s.className = `status ${cls}`;
}

function setSelectionBar(text: string | null): void {
  const bar = el('selection-bar');
  const label = el<HTMLSpanElement>('selection-label');
  if (text) {
    const preview = text.length > 60 ? text.slice(0, 60) + '…' : text;
    label.textContent = `Context: "${preview}"`;
    bar.classList.remove('hidden');
  } else {
    bar.classList.add('hidden');
  }
  state.selection = text;
}

// --- Network calls (via background) ---

async function ping(): Promise<void> {
  setStatus('● Connecting…', 'checking');
  try {
    const resp = await chrome.runtime.sendMessage({ type: 'PING' }) as { ok: boolean };
    if (resp.ok) {
      setStatus('● Connected', 'online');
      state.serverOnline = true;
      await chrome.runtime.sendMessage({
        type: 'CREATE_CONV',
        convId: state.convId,
        systemMsg: 'You are a helpful assistant. You are accessible via a browser extension called gptme. Be concise.',
      });
    } else {
      setStatus('● Server offline — run `gptme server`', 'offline');
      state.serverOnline = false;
    }
  } catch {
    setStatus('● Connection error', 'offline');
    state.serverOnline = false;
  }
}

async function sendMessage(): Promise<void> {
  if (!state.serverOnline || state.generating) return;

  const input = el<HTMLTextAreaElement>('input');
  const question = input.value.trim();
  if (!question) return;

  input.value = '';
  state.generating = true;
  el<HTMLButtonElement>('send-btn').disabled = true;

  // Build content with optional selection context
  let content = question;
  if (state.selection) {
    content = `[Page context]\nSelected text:\n> ${state.selection}\n\n${question}`;
  }

  appendMessage('user', question);
  const assistantDiv = appendMessage('assistant', '');
  const textEl = assistantDiv.querySelector('.text') as HTMLElement;
  state.currentAssistantText = '';

  chrome.runtime.sendMessage({
    type: 'SEND_AND_STEP',
    convId: state.convId,
    content,
  }).catch(() => {
    appendMessage('system', 'Failed to send message. Is the server running?');
    state.generating = false;
    el<HTMLButtonElement>('send-btn').disabled = false;
  });
}

function clearConversation(): void {
  const oldConvId = state.convId;
  state.convId = `gptme-ext-${Date.now()}`;
  state.generating = false;
  state.currentAssistantText = '';
  el('messages').innerHTML = '';
  el<HTMLButtonElement>('send-btn').disabled = false;
  setSelectionBar(null);
  chrome.runtime.sendMessage({ type: 'CANCEL', convId: oldConvId }).catch(() => {});
  ping();
}

// --- Incoming messages from background ---

chrome.runtime.onMessage.addListener((msg: Record<string, unknown>) => {
  if (msg.type === 'TOKEN' && msg.convId === state.convId) {
    state.currentAssistantText += msg.token as string;
    const messages = el('messages');
    const last = messages.querySelector('.message.assistant:last-child .text');
    if (last) last.textContent = state.currentAssistantText;
    messages.scrollTop = messages.scrollHeight;
    return;
  }

  if (msg.type === 'DONE' && msg.convId === state.convId) {
    state.generating = false;
    el<HTMLButtonElement>('send-btn').disabled = false;
    el<HTMLTextAreaElement>('input').focus();
    return;
  }

  if (msg.type === 'ERROR' && msg.convId === state.convId) {
    state.generating = false;
    el<HTMLButtonElement>('send-btn').disabled = false;
    appendMessage('system', `Error: ${String(msg.error)}`);
    return;
  }

  if (msg.type === 'SELECTION_UPDATE') {
    setSelectionBar(msg.selection as string);
    return;
  }
});

// --- Init ---

document.addEventListener('DOMContentLoaded', async () => {
  el('send-btn').addEventListener('click', () => { void sendMessage(); });
  el('clear-btn').addEventListener('click', clearConversation);
  el('clear-selection').addEventListener('click', () => setSelectionBar(null));

  el<HTMLTextAreaElement>('input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  });

  // Load any existing selection from when the panel was closed
  try {
    const data = await chrome.runtime.sendMessage({ type: 'GET_SELECTION' }) as {
      lastSelection?: string;
    };
    if (data.lastSelection) setSelectionBar(data.lastSelection);
  } catch {
    // background may not be ready yet
  }

  await ping();
});
