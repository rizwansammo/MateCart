import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

// One flat config for every TypeScript/JavaScript workspace package. Python services use ruff.
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    settings: {
      next: { rootDir: ['apps/commerce-web/', 'apps/platform-console/'] },
    },
  },
  {
    rules: {
      '@typescript-eslint/consistent-type-imports': 'error',
      'no-console': ['warn', { allow: ['warn', 'error'] }],
    },
  },
  {
    // Node CLI scripts report to the terminal by design.
    files: ['scripts/**/*.mjs'],
    rules: { 'no-console': 'off' },
  },
  {
    // Auth navigation intentionally reloads the document to discard transient setup secrets,
    // recovery codes and stale realm state. shared-ui is also independent of the Next router.
    files: ['packages/shared-ui/src/**/*.tsx', 'apps/*/src/components/*-app.tsx'],
    rules: {
      '@next/next/no-html-link-for-pages': 'off',
      '@next/next/no-location-assign-relative-destination': 'off',
    },
  },
  globalIgnores([
    '**/.next/**',
    '**/node_modules/**',
    '**/coverage/**',
    'services/**',
    '**/next-env.d.ts',
  ]),
]);
