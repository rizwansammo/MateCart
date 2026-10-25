import 'server-only';

import { isReadinessStatus, type ReadinessStatus } from '@matecart/shared-contracts';

import { readServerUrl } from './env';

export type ApiReadiness =
  | { state: 'not-configured' }
  | { state: 'unreachable' }
  | { state: 'invalid-response'; httpStatus: number }
  | { state: 'reported'; httpStatus: number; body: ReadinessStatus };

/** Asks control-api whether it and its dependencies are ready. Never throws for network failures. */
export async function getControlApiReadiness(): Promise<ApiReadiness> {
  const baseUrl = readServerUrl('CONTROL_API_INTERNAL_URL');
  if (!baseUrl) return { state: 'not-configured' };

  let response: Response;
  try {
    response = await fetch(new URL('/api/health/ready/', baseUrl), {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
  } catch {
    return { state: 'unreachable' };
  }

  const body: unknown = await response.json().catch(() => null);
  return isReadinessStatus(body)
    ? { state: 'reported', httpStatus: response.status, body }
    : { state: 'invalid-response', httpStatus: response.status };
}
