import 'server-only';

type ServerUrlName = 'CONTROL_API_INTERNAL_URL';

/** Reads an optional absolute URL from server-side environment. Returns null when unset; throws when malformed. */
export function readServerUrl(name: ServerUrlName): URL | null {
  const raw = process.env[name]?.trim();
  if (!raw) return null;
  try {
    return new URL(raw);
  } catch {
    throw new Error(`${name} must be an absolute URL, received "${raw}".`);
  }
}
