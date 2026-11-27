import './polyfills';
import { createRoot } from 'react-dom/client';

import './index.css';
import { isDemoMode } from './utils/connectionConfig';
import { setupLogging } from './utils/logging';
import { isTauriEnvironment } from './utils/tauri';
import { initializeSameOriginServer } from './stores/servers';

// Initialize logging as early as possible
setupLogging()
  .then(() => {
    console.info('Logging setup complete');
  })
  .catch((err) => {
    console.error('Failed to setup logging:', err);
  });

// Early localhost probe to trigger macOS local-network permission dialog
// at app startup, before the user reaches the Connect step.
// See: gptme/gptme#2236
(function probeLocalhost() {
  // Only probe in Tauri desktop app; skip in regular browsers
  if (typeof window === 'undefined' || !isTauriEnvironment() || isDemoMode()) {
    return;
  }

  const MAX_RETRIES = 10;
  const INTERVAL_MS = 500;
  let attempts = 0;

  const tryConnect = () => {
    attempts++;
    fetch('http://127.0.0.1:5700/api/v2', { mode: 'no-cors', cache: 'no-store' })
      .then(() => {
        console.info('[Probe] localhost connection established');
      })
      .catch(() => {
        if (attempts < MAX_RETRIES) {
          setTimeout(tryConnect, INTERVAL_MS);
        }
      });
  };

  // Start probing immediately; this intentionally races with server startup
  // so that the macOS permission dialog appears early, not mid-flow.
  tryConnect();
})();

// Decide the default before mounting clients or the setup wizard. In particular,
// do not briefly probe the viewer's loopback server when this page serves an API.
async function mountApp() {
  if (!isTauriEnvironment() && !isDemoMode()) {
    await initializeSameOriginServer();
  }
  // ApiContext consumes #userToken at module initialization. Load it only
  // after discovery so the token applies to this origin, not the old preset.
  const { default: App } = await import('./App.tsx');
  createRoot(document.getElementById('root')!).render(<App />);
}

void mountApp();
