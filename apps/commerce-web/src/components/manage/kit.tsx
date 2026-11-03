'use client';

import { useCallback, useEffect, useId, useState } from 'react';
import type { ReactNode, SelectHTMLAttributes } from 'react';
import { Alert, Button, Dialog, PasswordField, errorMessage } from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';
import { formatMoney } from '@/lib/money';

export { api };

/** Shared building blocks for the merchant workspace. Figures always come from the API. */

export function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await load());
      setError('');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [load]);
  useEffect(() => {
    void Promise.resolve().then(reload);
  }, [reload]);
  return { data, setData, error, loading, reload };
}

export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

export function newKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function money(amount: string | null | undefined, currency: string | null | undefined) {
  if (amount === null || amount === undefined || !currency) return '—';
  return formatMoney(amount, currency);
}

/** "5.000" → "5", "1.250" → "1.25": quantities without float conversion. */
export function quantity(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}

const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

export function date(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? value : DATE.format(parsed);
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : DATE_TIME.format(parsed);
}

/** A local calendar date as YYYY-MM-DD, offset by ``days``. */
export function isoDay(days = 0): string {
  const now = new Date();
  now.setDate(now.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function label(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—';
  return map[value] ?? value.replaceAll('_', ' ');
}

export const ORDER_STATUS: Record<string, string> = {
  pending_payment: 'Awaiting payment',
  placed: 'New',
  confirmed: 'Confirmed',
  processing: 'Processing',
  cancelled: 'Cancelled',
};
export const PAYMENT_STATUS: Record<string, string> = {
  unpaid: 'Unpaid',
  paid: 'Paid',
  partially_refunded: 'Partly refunded',
  refunded: 'Refunded',
  failed: 'Not completed',
};
export const PAYMENT_METHOD: Record<string, string> = {
  cod: 'Cash on delivery',
  online: 'Online payment',
};
export const SHIPMENT_STATUS: Record<string, string> = {
  ready: 'Ready to pick',
  picking: 'Picking',
  packed: 'Packed',
  ready_for_dispatch: 'Ready for dispatch',
  dispatched: 'Dispatched',
  in_transit: 'In transit',
  delivered: 'Delivered',
  delivery_failed: 'Delivery failed',
  return_to_sender: 'Returned to sender',
  cancelled: 'Cancelled',
};
export const SOURCE: Record<string, string> = {
  storefront: 'Online store',
  facebook: 'Facebook',
  whatsapp: 'WhatsApp',
  phone: 'Phone call',
  in_store: 'In store',
  other: 'Other',
};
export const CASE_KIND: Record<string, string> = {
  return: 'Return',
  exchange: 'Exchange',
  warranty: 'Warranty',
  support: 'Support',
};
export const CASE_STATUS: Record<string, string> = {
  requested: 'Requested',
  approved: 'Approved',
  rejected: 'Rejected',
  received: 'Goods received',
  inspected: 'Inspected',
  awaiting_refund: 'Awaiting refund',
  replacement_pending: 'Replacement pending',
  resolved: 'Resolved',
  closed: 'Closed',
};
export const FRAUD_STATUS: Record<string, string> = {
  clear: 'No flags',
  pending: 'Review suggested',
  cleared: 'Cleared',
  monitor: 'Monitor',
  confirmed_risk: 'Risk confirmed',
};
export const PRODUCT_STATUS: Record<string, string> = {
  draft: 'Draft',
  active: 'Published',
  archived: 'Archived',
};

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';
const TONES: Record<Tone, string> = {
  neutral: 'bg-canvas text-ink',
  info: 'bg-info-bg text-info-fg',
  success: 'bg-success-bg text-success-fg',
  warning: 'bg-warning-bg text-warning-fg',
  danger: 'bg-danger-bg text-danger-fg',
};
const STATUS_TONE: Record<string, Tone> = {
  placed: 'info',
  pending_payment: 'warning',
  confirmed: 'info',
  processing: 'warning',
  cancelled: 'danger',
  unpaid: 'warning',
  paid: 'success',
  partially_refunded: 'neutral',
  refunded: 'neutral',
  failed: 'danger',
  delivered: 'success',
  delivery_failed: 'danger',
  return_to_sender: 'danger',
  dispatched: 'info',
  in_transit: 'info',
  requested: 'info',
  approved: 'info',
  rejected: 'danger',
  resolved: 'success',
  closed: 'neutral',
  active: 'success',
  draft: 'neutral',
  archived: 'neutral',
  pending: 'warning',
  confirmed_risk: 'danger',
  clear: 'success',
  cleared: 'success',
  succeeded: 'success',
};

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center rounded-badge px-2 py-0.5 text-xs font-semibold whitespace-nowrap ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ value, map }: { value: string | null; map: Record<string, string> }) {
  if (!value) return <span className="text-xs text-muted">—</span>;
  return <Badge tone={STATUS_TONE[value] ?? 'neutral'}>{label(map, value)}</Badge>;
}

export function PageHeader({
  eyebrow,
  title,
  intro,
  actions,
}: {
  eyebrow?: string;
  title: string;
  intro?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && (
          <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">{eyebrow}</p>
        )}
        <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-[28px]">{title}</h1>
        {intro && <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{intro}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  actions,
  children,
  flush = false,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="min-w-0 rounded-base border border-line bg-surface">
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          {title && <h2 className="text-base font-bold">{title}</h2>}
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
      )}
      <div className={flush ? '' : 'p-5'}>{children}</div>
    </section>
  );
}

export function Stat({
  label: name,
  value,
  hint,
  href,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  href?: string;
  tone?: Tone;
}) {
  const body = (
    <>
      <p className="text-xs font-semibold tracking-wide text-muted uppercase">{name}</p>
      <p className="mt-2 text-2xl font-bold tracking-tight tabular-nums">{value}</p>
      {hint && (
        <p className={`mt-1 text-xs ${tone === 'danger' ? 'text-danger-fg' : 'text-muted'}`}>
          {hint}
        </p>
      )}
    </>
  );
  const className = 'block rounded-base border border-line bg-surface p-4';
  return href ? (
    <a href={href} className={`${className} hover:border-ink`}>
      {body}
    </a>
  ) : (
    <div className={className}>{body}</div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-5 py-10 text-center">
      <p className="text-sm font-semibold">{title}</p>
      {children && (
        <div className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">{children}</div>
      )}
    </div>
  );
}

export function Loader({ children = 'Loading…' }: { children?: ReactNode }) {
  return (
    <p role="status" className="px-5 py-8 text-sm text-muted">
      {children}
    </p>
  );
}

/** A table that scrolls sideways on phones when it has many columns; short tables just fit. */
export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className={`w-full text-left text-sm ${head.length > 4 ? 'min-w-[640px]' : ''}`}>
        <thead className="border-b border-line bg-canvas text-xs font-semibold tracking-wide text-muted uppercase">
          <tr>
            {head.map((name) => (
              <th key={name} scope="col" className="px-4 py-2.5 whitespace-nowrap">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function Cell({ children, className = '' }: { children?: ReactNode; className?: string }) {
  return <td className={`px-4 py-3 align-top ${className}`}>{children}</td>;
}

export function Pagination({
  page,
  pageSize,
  count,
  onPage,
}: {
  page: number;
  pageSize: number;
  count: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  if (count <= pageSize) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-sm">
      <span className="text-muted">
        Page {page} of {pages} · {count} total
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}

export function SelectField({
  label: name,
  options,
  hint,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  options: { value: string; label: string }[];
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <label className="text-[13px] font-semibold" htmlFor={id}>
        {name}
      </label>
      <select
        {...props}
        id={id}
        className="h-[46px] w-full min-w-0 rounded-base border border-line-strong bg-surface px-3 text-[15px] outline-none focus:border-brand-deep focus:ring-3 focus:ring-brand-tint disabled:bg-canvas"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint && <p className="text-xs leading-5 text-muted">{hint}</p>}
    </div>
  );
}

export function Filters({ children }: { children: ReactNode }) {
  return (
    <div className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 lg:grid-cols-4">
      {children}
    </div>
  );
}

export function Feedback({ error, notice }: { error?: string; notice?: string }) {
  if (!error && !notice) return null;
  return (
    <div className="mb-5 grid gap-3">
      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
    </div>
  );
}

export function Detail({ label: name, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs font-semibold text-muted">{name}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

/**
 * Money and access changes need a fresh password confirmation (recent authentication). The
 * action runs only after the server accepts the password, and the result is reported by the
 * caller only after the API confirms it.
 */
export function ReauthDialog({
  title,
  description,
  confirmLabel = 'Confirm',
  onConfirm,
  onClose,
}: {
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <Dialog title={title} onClose={onClose}>
      <form
        className="grid gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          void (async () => {
            try {
              await api.post('/api/manage/account/reauthenticate/', { password });
              setPassword('');
              await onConfirm();
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          })();
        }}
      >
        <div className="text-sm leading-6 text-muted">{description}</div>
        <PasswordField
          label="Your password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <Alert>{error}</Alert>}
        <div className="flex justify-end gap-3">
          <Button variant="secondary" type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={busy}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
