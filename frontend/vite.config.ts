import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig(({ command }) => ({
  // Django serves the build from /static/dashboard/dist/, so lazily loaded
  // chunks must be requested from there rather than the site root.
  base: command === 'build' ? '/static/dashboard/dist/' : '/',
  plugins: [react()],
  build: {
    outDir: path.resolve(import.meta.dirname, '../dashboard/static/dashboard/dist'),
    emptyOutDir: true,
    // The Django template links one stylesheet, so keep all CSS in it. Split
    // CSS would leave styles for shared chunks unloaded on first paint.
    cssCodeSplit: false,
    rolldownOptions: {
      input: path.resolve(import.meta.dirname, 'src/main.tsx'),
      output: {
        entryFileNames: 'assets/app.js',
        chunkFileNames: 'assets/[name].js',
        // The single stylesheet keeps the name the Django template links.
        assetFileNames: (asset) => (asset.names?.[0]?.endsWith('.css') ? 'assets/main.css' : 'assets/[name].[ext]'),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8001',
    },
  },
}));
