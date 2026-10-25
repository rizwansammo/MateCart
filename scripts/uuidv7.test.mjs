import assert from 'node:assert/strict';
import { test } from 'node:test';

import { uuidv7 } from './uuidv7.mjs';

const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test('uuidv7 has the version 7 layout and RFC 9562 variant', () => {
  for (let index = 0; index < 1000; index += 1) {
    assert.match(uuidv7(), UUID_V7);
  }
});

test('uuidv7 embeds the millisecond timestamp and sorts by time', () => {
  const now = 1_761_400_000_000;
  const value = uuidv7(now);
  assert.equal(Number.parseInt(value.replaceAll('-', '').slice(0, 12), 16), now);
  assert.ok(uuidv7(now) < uuidv7(now + 1));
});

test('uuidv7 values are unique', () => {
  const values = new Set(Array.from({ length: 10_000 }, () => uuidv7(1_761_400_000_000)));
  assert.equal(values.size, 10_000);
});
