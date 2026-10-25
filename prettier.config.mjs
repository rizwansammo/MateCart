/** @type {import('prettier').Config} */
const config = {
  singleQuote: true,
  printWidth: 100,
  plugins: ['prettier-plugin-tailwindcss'],
  overrides: [
    {
      files: 'apps/commerce-web/**',
      options: { tailwindStylesheet: './apps/commerce-web/src/app/globals.css' },
    },
    {
      files: 'apps/platform-console/**',
      options: { tailwindStylesheet: './apps/platform-console/src/app/globals.css' },
    },
    {
      files: 'packages/shared-ui/**',
      options: { tailwindStylesheet: './apps/commerce-web/src/app/globals.css' },
    },
  ],
};

export default config;
