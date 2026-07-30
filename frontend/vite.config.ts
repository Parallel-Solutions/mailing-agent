import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:9806', changeOrigin: true },
      '/consent': { target: 'http://localhost:9806', changeOrigin: true },
      '/public': { target: 'http://localhost:9806', changeOrigin: true },
      '/login': { target: 'http://localhost:9806', changeOrigin: true },
      '/register': { target: 'http://localhost:9806', changeOrigin: true },
    },
  },
  optimizeDeps: {
    include: ['grapesjs', 'grapesjs-preset-newsletter'],
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'html'],
      reportsDirectory: '../.test-runtime/frontend-coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/*.spec.{ts,tsx}',
        'src/test/**',
        'src/**/*.d.ts',
      ],
    },
  },
});
