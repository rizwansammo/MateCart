import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

// Storefront unit and component tests (Phase 06). Components are rendered with jsdom; data
// fetching is tested through pure helpers, so no server or API is needed.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // `server-only` throws outside React Server Components; tests import those modules directly.
      'server-only': fileURLToPath(new URL('./src/test/server-only.ts', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
});
