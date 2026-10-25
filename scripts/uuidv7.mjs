// RFC 9562 UUIDv7: 48-bit Unix milliseconds, version 7, 74 random bits, RFC variant.
// Used for identifiers generated outside the database (MATECART_TENANT_ID), so they follow the
// same time-ordered format as database primary keys (docs/DECISIONS.md D-036).

import { randomBytes } from 'node:crypto';

export function uuidv7(nowMs = Date.now()) {
  const bytes = randomBytes(16);
  const timestamp = BigInt(nowMs);
  for (let index = 0; index < 6; index += 1) {
    bytes[index] = Number((timestamp >> BigInt(8 * (5 - index))) & 0xffn);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x70; // version 7
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 9562 variant
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
