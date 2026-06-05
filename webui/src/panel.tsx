/**
 * Extension panel entrypoint — React root for the Chrome extension side panel.
 *
 * This is a standalone React app that reuses webui components
 * (ChatInput, ChatMessage, etc.) but communicates with the extension's
 * background service worker via chrome.runtime instead of the webui's
 * React Query / API client.
 *
 * It imports the shared CSS (`index.css`) but uses its own mount point
 * and avoids routing, the app shell, and server-discovery logic.
 */

import { createRoot } from 'react-dom/client';
import ExtensionChat from './components/ExtensionChat';
import './index.css';

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(<ExtensionChat />);
}
