// Phase 06.2 (D-069): the theme catalogue exists three times by design (commerce-api validates
// assignments, shared-contracts lists identities, shared-ui holds the visual definitions). This
// keeps the API's generated enum and the shared catalogue identical, in the same order.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const root = new URL('../', import.meta.url);
const read = (path) => readFileSync(new URL(path, root), 'utf8');

test('the API theme enum matches the shared theme catalogue', () => {
  const schema = read('services/commerce-api/openapi.yaml');
  const block = /\n {4}ActiveThemeEnum:\n {6}enum:\n((?: {6}- .+\n)+)/.exec(schema);
  assert.ok(block, 'ActiveThemeEnum is missing from openapi.yaml');
  const fromApi = block[1]
    .trim()
    .split('\n')
    .map((line) => line.replace(/^\s*- /, '').trim());
  const catalogue = JSON.parse(read('packages/shared-contracts/src/themes.json'));
  assert.deepEqual(
    fromApi,
    catalogue.themes.map((theme) => theme.id),
  );
});

test('every preset in the shared catalogue has exactly one default theme', () => {
  const { themes } = JSON.parse(read('packages/shared-contracts/src/themes.json'));
  const presets = ['fashion', 'grocery', 'electronics', 'general', 'books'];
  for (const preset of presets) {
    assert.equal(themes.filter((t) => t.preset === preset && t.default).length, 1, preset);
  }
  assert.ok(themes.every((t) => presets.includes(t.preset)));
});
