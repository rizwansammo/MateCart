/**
 * Health endpoint contracts.
 *
 * Hand-maintained mirror of the OpenAPI schemas committed at services/<service>/openapi.yaml.
 * Keep both in sync until TypeScript generation from OpenAPI is introduced (see docs/DECISIONS.md).
 */

export type ServiceName = 'commerce-api' | 'control-api' | 'commerce-web' | 'platform-console';

/** Result of a single readiness dependency check. Failure details are logged, never returned. */
export type CheckState = 'ok' | 'error';

/** `GET /api/health/live/` (APIs) and `GET /healthz` (web apps). The process is up. */
export interface LivenessStatus {
  status: 'ok';
  service: ServiceName;
}

/** `GET /api/health/ready/`. HTTP 200 when every check is ok, otherwise HTTP 503. */
export interface ReadinessStatus {
  status: 'ok' | 'unavailable';
  service: ServiceName;
  checks: Record<string, CheckState>;
}

const SERVICE_NAMES: readonly string[] = [
  'commerce-api',
  'control-api',
  'commerce-web',
  'platform-console',
] satisfies readonly ServiceName[];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isServiceName(value: unknown): value is ServiceName {
  return typeof value === 'string' && SERVICE_NAMES.includes(value);
}

export function isLivenessStatus(value: unknown): value is LivenessStatus {
  return isRecord(value) && value.status === 'ok' && isServiceName(value.service);
}

export function isReadinessStatus(value: unknown): value is ReadinessStatus {
  if (!isRecord(value) || !isServiceName(value.service)) return false;
  if (value.status !== 'ok' && value.status !== 'unavailable') return false;
  const { checks } = value;
  return (
    isRecord(checks) && Object.values(checks).every((state) => state === 'ok' || state === 'error')
  );
}
