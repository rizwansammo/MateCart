#!/usr/bin/env node
// Checks every MateCart development service from the host and exits non-zero if any fails.
//   pnpm health               Docker stack + both Next.js apps (start them with `pnpm dev`)
//   pnpm health --skip-web    Docker stack only
// API ports are read from .env so custom port choices keep working.

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

function readDotEnv(path) {
  if (!existsSync(path)) return {};
  const values = {};
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const match = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match) values[match[1]] = match[2];
  }
  return values;
}

const env = { ...readDotEnv(join(root, '.env')), ...process.env };
const commerceApi = `http://127.0.0.1:${env.MATECART_COMMERCE_API_PORT ?? '8400'}`;
const controlApi = `http://127.0.0.1:${env.MATECART_CONTROL_API_PORT ?? '8410'}`;
const skipWeb = process.argv.includes('--skip-web');

const checks = [
  { name: 'commerce-api live', url: `${commerceApi}/api/health/live/` },
  { name: 'commerce-api ready', url: `${commerceApi}/api/health/ready/` },
  { name: 'control-api live', url: `${controlApi}/api/health/live/` },
  { name: 'control-api ready', url: `${controlApi}/api/health/ready/` },
  ...(skipWeb
    ? []
    : [
        { name: 'commerce-web', url: 'http://127.0.0.1:3400/healthz' },
        { name: 'platform-console', url: 'http://127.0.0.1:3410/healthz' },
      ]),
];

async function run({ name, url }) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(5000) });
    const body = await response.json().catch(() => null);
    const ok = response.ok && body?.status === 'ok';
    return { name, url, ok, detail: `${response.status} ${JSON.stringify(body)}` };
  } catch (error) {
    return { name, url, ok: false, detail: `unreachable (${error.cause?.code ?? error.name})` };
  }
}

const results = await Promise.all(checks.map(run));
for (const { name, url, ok, detail } of results) {
  console.log(`${ok ? 'OK  ' : 'FAIL'}  ${name.padEnd(20)} ${url}\n      ${detail}`);
}

const failed = results.filter((result) => !result.ok).length;
console.log(
  failed === 0
    ? '\nAll checks passed.'
    : `\n${failed} check(s) failed. See docs/DEVELOPMENT_SETUP.md.`,
);
process.exitCode = failed === 0 ? 0 : 1;
