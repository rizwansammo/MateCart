import type { ApiReadiness } from '@/lib/commerce-api';

const STATE_MESSAGES: Record<Exclude<ApiReadiness['state'], 'reported'>, string> = {
  'not-configured': 'COMMERCE_API_INTERNAL_URL is not set. Run `pnpm setup:env`.',
  unreachable: 'The API did not respond. Is the Docker stack running (`pnpm stack:up`)?',
  'invalid-response': 'The API responded with an unexpected payload.',
};

function Badge({ ok, children }: { ok: boolean; children: string }) {
  return (
    <span
      className={`rounded-badge px-1.5 py-0.5 text-xs font-semibold ${
        ok ? 'bg-success-bg text-success-fg' : 'bg-danger-bg text-danger-fg'
      }`}
    >
      {children}
    </span>
  );
}

export function ReadinessSummary({ label, readiness }: { label: string; readiness: ApiReadiness }) {
  if (readiness.state !== 'reported') {
    return (
      <div className="mt-6 border-t border-line pt-4 text-sm">
        <p className="flex items-center justify-between gap-4 font-semibold">
          {label} <Badge ok={false}>unavailable</Badge>
        </p>
        <p className="mt-1 text-muted">{STATE_MESSAGES[readiness.state]}</p>
      </div>
    );
  }

  const { body } = readiness;
  return (
    <div className="mt-6 border-t border-line pt-4 text-sm">
      <p className="flex items-center justify-between gap-4 font-semibold">
        {label} <Badge ok={body.status === 'ok'}>{body.status}</Badge>
      </p>
      <ul className="mt-2 space-y-1 font-mono text-xs text-ink-2">
        {Object.entries(body.checks).map(([name, state]) => (
          <li key={name} className="flex justify-between">
            <span>{name}</span>
            <span className={state === 'ok' ? 'text-success-fg' : 'text-danger-fg'}>{state}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
