import './polyfills';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import './index.css';
import { setupLogging } from './utils/logging';

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
  if (typeof window === 'undefined' || !window.__TAURI__) {
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

createRoot(document.getElementById('root')!).render(<App />);
