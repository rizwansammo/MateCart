#!/usr/bin/env node
// Creates local, git-ignored environment files from the committed examples.
//   .env.example                 -> .env          (__GENERATE__ values become random secrets)
//   apps/<app>/.env.example      -> apps/<app>/.env.local
// Existing files are never overwritten, so re-running is always safe.

import { randomBytes } from 'node:crypto';
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

function createFrom(example, target, transform) {
  const label = relative(root, target);
  if (existsSync(target)) {
    console.log(`kept     ${label} (already exists)`);
    return;
  }
  if (transform) {
    writeFileSync(target, transform(readFileSync(example, 'utf8')));
  } else {
    copyFileSync(example, target);
  }
  console.log(`created  ${label}`);
}

// 64 hex characters: URL-safe for connection strings and strong enough for Django's SECRET_KEY.
const generateSecrets = (text) =>
  text.replaceAll('__GENERATE__', () => randomBytes(32).toString('hex'));

createFrom(join(root, '.env.example'), join(root, '.env'), generateSecrets);
for (const app of ['commerce-web', 'platform-console']) {
  const dir = join(root, 'apps', app);
  createFrom(join(dir, '.env.example'), join(dir, '.env.local'));
}
