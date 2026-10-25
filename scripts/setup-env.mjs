#!/usr/bin/env node
// Creates and updates local, git-ignored environment files from the committed examples.
//   .env.example                 -> .env          (__GENERATE__ / __GENERATE_UUID__ become values)
//   apps/<app>/.env.example      -> apps/<app>/.env.local
// Existing values are never changed. Keys added to an example later are appended to the
// existing file, so re-running after pulling new changes is always safe.

import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { uuidv7 } from './uuidv7.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

// 64 hex characters: URL-safe for connection strings and strong enough for Django's SECRET_KEY.
const generateValues = (text) =>
  text
    .replaceAll('__GENERATE_UUID__', () => uuidv7())
    .replaceAll('__GENERATE__', () => randomBytes(32).toString('hex'));

const keyOf = (line) => /^\s*([A-Z0-9_]+)\s*=/.exec(line)?.[1];

function sync(example, target) {
  const label = relative(root, target);
  const exampleText = readFileSync(example, 'utf8');
  if (!existsSync(target)) {
    writeFileSync(target, generateValues(exampleText));
    console.log(`created  ${label}`);
    return;
  }
  const current = readFileSync(target, 'utf8');
  const present = new Set(current.split(/\r?\n/).map(keyOf).filter(Boolean));
  const missing = exampleText
    .split(/\r?\n/)
    .filter((line) => keyOf(line) && !present.has(keyOf(line)));
  if (missing.length === 0) {
    console.log(`kept     ${label} (up to date)`);
    return;
  }
  const separator = current.endsWith('\n') ? '' : '\n';
  writeFileSync(target, `${current}${separator}${generateValues(missing.join('\n'))}\n`);
  console.log(`updated  ${label} (added ${missing.map(keyOf).join(', ')})`);
}

sync(join(root, '.env.example'), join(root, '.env'));
for (const app of ['commerce-web', 'platform-console']) {
  const dir = join(root, 'apps', app);
  sync(join(dir, '.env.example'), join(dir, '.env.local'));
}
