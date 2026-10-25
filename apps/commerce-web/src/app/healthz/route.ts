import type { LivenessStatus } from '@matecart/shared-contracts';

export const dynamic = 'force-dynamic';

// Liveness only: it never calls other services, so it stays cheap and safe to poll.
export function GET(): Response {
  const body: LivenessStatus = { status: 'ok', service: 'commerce-web' };
  return Response.json(body, { headers: { 'Cache-Control': 'no-store' } });
}
