import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import { fileURLToPath } from 'url';
import { componentTagger } from 'lovable-tagger';

const isExtensionBuild = process.env.VITE_EXTENSION_BUILD === '1';

// Package -> vendor chunk, applied to the package's files under node_modules.
const VENDOR_CHUNK_BY_PACKAGE: Record<string, string> = {
  react: 'vendor-react',
  'react-dom': 'vendor-react',
  'react-router': 'vendor-react',
  'react-router-dom': 'vendor-react',
  '@tanstack/react-query': 'vendor-query',
  '@legendapp/state': 'vendor-legend',
  '@radix-ui/react-accordion': 'vendor-radix',
  '@radix-ui/react-dialog': 'vendor-radix',
  '@radix-ui/react-dropdown-menu': 'vendor-radix',
  '@radix-ui/react-popover': 'vendor-radix',
  '@radix-ui/react-select': 'vendor-radix',
  '@radix-ui/react-tabs': 'vendor-radix',
  '@radix-ui/react-tooltip': 'vendor-radix',
  'lucide-react': 'vendor-icons',
  recharts: 'vendor-recharts',
};

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
        // Vite 8 (rolldown) only accepts manualChunks as a function.
        manualChunks(id: string) {
          const match = id.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//);
          return match ? VENDOR_CHUNK_BY_PACKAGE[match[1]] : undefined;
        },
      },
    },
  },
}));
