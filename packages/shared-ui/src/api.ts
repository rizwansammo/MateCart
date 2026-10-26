import { isApiErrorBody } from '@matecart/shared-contracts';

/**
 * Browser client for the MateCart APIs, reached on the page's own origin under /api/.
 *
 * Sessions live in HttpOnly cookies the page cannot read; nothing is kept in localStorage. Every
 * state-changing request carries the CSRF token from the (readable) CSRF cookie, fetched first
 * when missing. Errors always become an ApiError with a message that is safe to show.
 */

export const GENERIC_ERROR = 'Something went wrong on our side. Please try again in a moment.';
export const NETWORK_ERROR = "We couldn't reach the server. Check your connection and try again.";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** First message per field for `validation_error` responses (`{ email: "Enter a valid…" }`). */
  get fieldErrors(): Record<string, string> {
    if (this.code !== 'validation_error' || typeof this.details !== 'object' || !this.details) {
      return {};
    }
    const result: Record<string, string> = {};
    for (const [field, value] of Object.entries(this.details as Record<string, unknown>)) {
      const first = Array.isArray(value) ? value[0] : value;
      if (typeof first === 'string') result[field] = first;
    }
    return result;
  }
}

/** A message for people, never raw server output. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'validation_error') {
      const fields = Object.values(error.fieldErrors);
      return fields[0] ?? 'Please check the highlighted fields and try again.';
    }
    return error.message;
  }
  return GENERIC_ERROR;
}

export function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return decodeURIComponent(rest.join('='));
  }
  return null;
}

export interface ApiClientOptions {
  /** Name of the CSRF cookie this API sets (commerce: mc_csrf, console: mc_console_csrf). */
  csrfCookie: string;
  csrfPath?: string;
  fetchImpl?: typeof fetch;
}

type Method = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

export interface ApiClient {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body: unknown): Promise<T>;
  put<T>(path: string, body: unknown): Promise<T>;
  delete<T>(path: string): Promise<T>;
  /** multipart/form-data upload (the browser sets the boundary). */
  upload<T>(path: string, form: FormData): Promise<T>;
}

export function createApiClient({
  csrfCookie,
  csrfPath = '/api/csrf/',
  fetchImpl,
}: ApiClientOptions): ApiClient {
  const doFetch: typeof fetch = (...args) => (fetchImpl ?? fetch)(...args);

  async function csrfToken(refresh = false): Promise<string> {
    const existing = refresh ? null : readCookie(csrfCookie);
    if (existing) return existing;
    const response = await doFetch(csrfPath, { credentials: 'same-origin', cache: 'no-store' });
    const body: unknown = await response.json().catch(() => null);
    const fromBody =
      typeof body === 'object' && body !== null && 'csrf_token' in body
        ? String((body as { csrf_token: unknown }).csrf_token)
        : '';
    return readCookie(csrfCookie) ?? fromBody;
  }

  async function request<T>(
    method: Method,
    path: string,
    body?: unknown,
    retry = true,
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';

    let response: Response;
    try {
      if (method !== 'GET') headers['X-CSRFToken'] = await csrfToken();
      response = await doFetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
        credentials: 'same-origin',
        cache: 'no-store',
      });
    } catch {
      throw new ApiError(0, 'network_error', NETWORK_ERROR);
    }

    if (response.status === 204) return undefined as T;
    // Bodiless successes (e.g. 202 "we sent it if the account exists") read as null.
    const data: unknown = await response.json().catch(() => null);
    if (response.ok) return data as T;

    if (isApiErrorBody(data)) {
      if (data.error.code === 'csrf_failed' && retry && method !== 'GET') {
        await csrfToken(true);
        return request<T>(method, path, body, false);
      }
      throw new ApiError(response.status, data.error.code, data.error.message, data.error.details);
    }
    throw new ApiError(response.status, 'server_error', GENERIC_ERROR);
  }

  return {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body ?? {}),
    patch: (path, body) => request('PATCH', path, body),
    put: (path, body) => request('PUT', path, body),
    delete: (path) => request('DELETE', path),
    upload: (path, form) => request('POST', path, form),
  };
}
