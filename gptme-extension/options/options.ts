// options.ts — read/write server URL and API key from chrome.storage.sync
export {};

const DEFAULT_URL = 'http://localhost:5700';

function el<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

document.addEventListener('DOMContentLoaded', async () => {
  const data = await chrome.storage.sync.get(['serverUrl', 'apiKey']) as {
    serverUrl?: string;
    apiKey?: string;
  };

  el<HTMLInputElement>('serverUrl').value = data.serverUrl ?? DEFAULT_URL;
  el<HTMLInputElement>('apiKey').value = data.apiKey ?? '';

  el('save-btn').addEventListener('click', async () => {
    const serverUrl = el<HTMLInputElement>('serverUrl').value.trim() || DEFAULT_URL;
    const apiKey = el<HTMLInputElement>('apiKey').value.trim() || undefined;

    await chrome.storage.sync.set({ serverUrl, apiKey });

    const saved = el('saved');
    saved.classList.add('visible');
    setTimeout(() => saved.classList.remove('visible'), 2500);
  });
});
