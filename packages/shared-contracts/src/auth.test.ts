import { describe, expect, it } from 'vitest';
import {
  challenge,
  customer,
  methods,
  securityStatus,
  staffSession,
  team,
  totpSetup,
} from './auth';

describe('authentication response boundaries', () => {
  it.each([null, [], 'ok', {}, { totp: 'false' }])(
    'rejects malformed security status (%j)',
    (value) => {
      expect(() => securityStatus(value)).toThrow();
    },
  );
  it('rejects an untrusted QR image source', () => {
    expect(() => totpSetup({ qr_svg: 'https://tracker.invalid/secret', secret: 'key' })).toThrow();
  });
  it('rejects a customer identity missing verified flags', () => {
    expect(() => customer({ id: 'id', full_name: 'Test' })).toThrow();
  });
  it('rejects permissions supplied as an object', () => {
    expect(() =>
      staffSession({
        user: { id: 'a', full_name: 'A', email: 'a@example.com' },
        role: 'owner',
        role_label: 'Owner',
        store_name: 'Store',
        permissions: { owner: true },
      }),
    ).toThrow();
  });
  it('requires explicit provider availability', () => {
    expect(() => methods({ phone: 'true', google: false, phone_country: 'BD' })).toThrow();
  });
  it('refuses a malformed challenge expiry', () => {
    expect(() =>
      challenge({ challenge_id: 'id', destination: 'x', resend_in: 60, expires_in: Infinity }),
    ).toThrow();
  });
  it('validates empty team lists', () => {
    expect(team({ members: [], invitations: [], roles: [] })).toEqual({
      members: [],
      invitations: [],
      roles: [],
    });
  });
});
