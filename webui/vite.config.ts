import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import { fileURLToPath } from 'url';
import { componentTagger } from 'lovable-tagger';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  //base: '/gptme-webui/',  // Add base URL for GitHub Pages (when served under user/org, not as its own subdomain)
  server:
    mode === 'development'
      ? {
          host: '::',
          port: 5701,
        }
      : undefined,
  plugins: [react(), mode === 'development' && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
}));
