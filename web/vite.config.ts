import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy: forward REST + WebSocket under /api to the agent on 127.0.0.1:8765.
// Production build is served by the backend from web/dist on the same origin,
// so the app always talks to a relative "/api" base.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8766',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // No .map files in production: they were ~1.9 MB per deploy over the frp link.
    sourcemap: false,
    rollupOptions: {
      output: {
        // Long-lived vendor chunk: deploys that only touch pages leave it cached.
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
        },
      },
    },
  },
});
