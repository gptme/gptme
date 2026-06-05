import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import { fileURLToPath } from 'url';
import { componentTagger } from 'lovable-tagger';

const isExtensionBuild = process.env.VITE_EXTENSION_BUILD === '1';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  //base: '/gptme-webui/',  // Add base URL for GitHub Pages (when served under user/org, not as its own subdomain)
  base: isExtensionBuild ? './' : undefined,
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
  build: {
    rollupOptions: {
      input: isExtensionBuild
        ? { panel: fileURLToPath(new URL('./panel.html', import.meta.url)) }
        : {
            main: fileURLToPath(new URL('./index.html', import.meta.url)),
            panel: fileURLToPath(new URL('./panel.html', import.meta.url)),
          },
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
        },
      },
    },
  },
}));
