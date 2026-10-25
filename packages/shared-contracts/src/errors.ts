/**
 * Error envelope returned by every MateCart API for non-2xx responses.
 * Mirrors the Django exception handler in services/<service>/<package>/core/exceptions.py.
 * The request id is returned in the `X-Request-ID` response header, not in the body.
 */
export interface ApiErrorBody {
  error: {
    /** Stable, machine-readable code such as `not_found`, `validation_error`, `server_error`. */
    code: string;
    /** Human-readable summary. Safe to show to users; never contains internals. */
    message: string;
    /** Field-level validation errors or other structured context, when available. */
    details?: unknown;
  };
}

export function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null) return false;
  const { error } = value as { error?: unknown };
  if (typeof error !== 'object' || error === null) return false;
  const { code, message } = error as { code?: unknown; message?: unknown };
  return typeof code === 'string' && typeof message === 'string';
}
