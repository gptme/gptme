// options.ts — server URL syncs, API key stays local-only
export {};

const DEFAULT_URL = 'http://localhost:5700';

interface StorageSync {
  serverUrl?: string;
  apiKey?: string;
}

interface StorageLocal {
  apiKey?: string;
}

function el<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

async function loadSettings(): Promise<{ serverUrl: string; apiKey: string }> {
  const [syncData, localData] = await Promise.all([
    chrome.storage.sync.get(['serverUrl', 'apiKey']) as Promise<StorageSync>,
    chrome.storage.local.get(['apiKey']) as Promise<StorageLocal>,
  ]);

  let apiKey = localData.apiKey ?? '';
  if (!apiKey && syncData.apiKey) {
    apiKey = syncData.apiKey;
    await chrome.storage.local.set({ apiKey });
  }
  if (syncData.apiKey) {
    await chrome.storage.sync.remove('apiKey');
  }

  return {
    serverUrl: syncData.serverUrl ?? DEFAULT_URL,
    apiKey,
  };
}

document.addEventListener('DOMContentLoaded', async () => {
  const data = await loadSettings();

  el<HTMLInputElement>('serverUrl').value = data.serverUrl ?? DEFAULT_URL;
  el<HTMLInputElement>('apiKey').value = data.apiKey ?? '';

  el('save-btn').addEventListener('click', async () => {
    const serverUrl = el<HTMLInputElement>('serverUrl').value.trim() || DEFAULT_URL;
    const apiKey = el<HTMLInputElement>('apiKey').value.trim() || undefined;

    await chrome.storage.sync.set({ serverUrl });
    if (apiKey) {
      await chrome.storage.local.set({ apiKey });
    } else {
      await chrome.storage.local.remove('apiKey');
    }
    await chrome.storage.sync.remove('apiKey');

    const saved = el('saved');
    saved.classList.add('visible');
    setTimeout(() => saved.classList.remove('visible'), 2500);
  });
});
