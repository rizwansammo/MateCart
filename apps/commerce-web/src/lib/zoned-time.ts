/**
 * Wall-clock times in the store's time zone (IANA), for `<input type="datetime-local">`.
 * Promotion windows are entered and shown in store time whatever the browser's own zone is;
 * the API always receives an absolute ISO 8601 instant.
 */

function parts(ms: number, timeZone: string): Record<string, number> {
  const out: Record<string, number> = {};
  const format = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  for (const part of format.formatToParts(new Date(ms))) {
    if (part.type !== 'literal') out[part.type] = Number(part.value);
  }
  return out;
}

function offset(ms: number, timeZone: string): number {
  const p = parts(ms, timeZone);
  const asUtc = Date.UTC(
    p.year ?? 1970,
    (p.month ?? 1) - 1,
    p.day ?? 1,
    p.hour ?? 0,
    p.minute ?? 0,
    p.second ?? 0,
  );
  return asUtc - Math.floor(ms / 1000) * 1000;
}

const pad = (n: number | undefined) => String(n ?? 0).padStart(2, '0');

/** ISO instant → "YYYY-MM-DDTHH:mm" in ``timeZone`` ('' for missing or invalid values). */
export function toZonedInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '';
  const p = parts(ms, timeZone);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** "YYYY-MM-DDTHH:mm" in ``timeZone`` → ISO instant (UTC), or null when malformed. */
export function fromZonedInput(local: string, timeZone: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
  if (!match) return null;
  const [, y, mo, d, h, mi] = match.map(Number);
  const guess = Date.UTC(y ?? 0, (mo ?? 1) - 1, d ?? 1, h ?? 0, mi ?? 0);
  let result = guess - offset(guess, timeZone);
  // A second pass settles instants next to a daylight-saving change.
  result = guess - offset(result, timeZone);
  return new Date(result).toISOString();
}

/** A human date-time in the store's zone, e.g. "4 Nov 2026, 18:00". */
export function formatZoned(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '—';
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(ms));
}
