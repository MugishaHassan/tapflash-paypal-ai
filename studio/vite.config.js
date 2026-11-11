import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The built bundle is committed under Django's static files, so the server needs no Node.
export default defineConfig({
  plugins: [react()],
  base: '/static/ops/studio/',
  build: {
    outDir: '../django/ops/static/ops/studio',
    emptyOutDir: true,
    chunkSizeWarningLimit: 20000,
    rollupOptions: {
      input: 'src/main.jsx',
      output: { entryFileNames: 'studio.js', chunkFileNames: 'studio-[name].js', assetFileNames: 'studio[extname]', inlineDynamicImports: true },
    },
  },
});
