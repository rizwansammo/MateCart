import { describe, expect, it } from 'vitest';

import { isApiErrorBody, isLivenessStatus, isReadinessStatus } from './index';

describe('isLivenessStatus', () => {
  it('accepts a valid liveness payload', () => {
    expect(isLivenessStatus({ status: 'ok', service: 'commerce-api' })).toBe(true);
  });

  it('rejects unknown services and wrong statuses', () => {
    expect(isLivenessStatus({ status: 'ok', service: 'mateerp' })).toBe(false);
    expect(isLivenessStatus({ status: 'down', service: 'control-api' })).toBe(false);
    expect(isLivenessStatus(null)).toBe(false);
  });
});

describe('isReadinessStatus', () => {
  it('accepts ok and unavailable payloads', () => {
    expect(
      isReadinessStatus({
        status: 'ok',
        service: 'commerce-api',
        checks: { database: 'ok', cache: 'ok', broker: 'ok' },
      }),
    ).toBe(true);
    expect(
      isReadinessStatus({
        status: 'unavailable',
        service: 'control-api',
        checks: { database: 'error' },
      }),
    ).toBe(true);
  });

  it('rejects malformed checks', () => {
    expect(
      isReadinessStatus({ status: 'ok', service: 'commerce-api', checks: { database: 'maybe' } }),
    ).toBe(false);
    expect(isReadinessStatus({ status: 'ok', service: 'commerce-api', checks: [] })).toBe(false);
  });
});

describe('isApiErrorBody', () => {
  it('accepts the error envelope', () => {
    expect(isApiErrorBody({ error: { code: 'not_found', message: 'Not found.' } })).toBe(true);
  });

  it('rejects other shapes', () => {
    expect(isApiErrorBody({ detail: 'Not found.' })).toBe(false);
    expect(isApiErrorBody({ error: { code: 404 } })).toBe(false);
  });
});
