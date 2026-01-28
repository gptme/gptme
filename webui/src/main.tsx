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

createRoot(document.getElementById('root')!).render(<App />);
