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
  globalIgnores([
    '**/.next/**',
    '**/node_modules/**',
    '**/coverage/**',
    'services/**',
    '**/next-env.d.ts',
  ]),
]);
