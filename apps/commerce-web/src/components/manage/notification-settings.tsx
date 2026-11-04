'use client';

import { useCallback, useState } from 'react';

import type {
  ConnectionStatus,
  DeliveryAttemptRow,
  EmailSettings,
  MailConnectionState,
  MailSecurity,
  NotificationDelivery,
  NotificationTemplate,
  StaffSession,
  TemplateLocale,
  TemplatePreview,
} from '@matecart/shared-contracts';
import {
  deliveryAttempts,
  deliveryPage,
  deliverySummary,
  emailSettings,
  notificationDelivery,
  notificationTemplate,
  notificationTemplates,
  smsSettings,
  templatePreview,
} from '@matecart/shared-contracts';
import {
  Button,
  ConfirmDialog,
  Dialog,
  PasswordField,
  TextField,
  errorMessage,
} from '@matecart/shared-ui';
import {
  Badge,
  Card,
  Cell,
  Detail,
  Empty,
  Feedback,
  Filters,
  Loader,
  PageHeader,
  Pagination,
  ReauthDialog,
  SelectField,
  Stat,
  Table,
  api,
  dateTime,
  query,
  useLoad,
} from './kit';
import { CATEGORY_LABEL } from './notifications';

const BASE = '/api/manage/notifications';
const TABS = [
  { key: 'email', label: 'Email' },
  { key: 'sms', label: 'SMS' },
  { key: 'templates', label: 'Templates' },
  { key: 'deliveries', label: 'Delivery history' },
] as const;
type Tab = (typeof TABS)[number]['key'];

export const STATUS_TEXT: Record<ConnectionStatus, string> = {
  not_configured: 'Not configured',
  unverified: 'Configured, not verified',
  awaiting_approval: 'Waiting for approval',
  connected: 'Connected',
  failed: 'Failed',
  revoked: 'Revoked',
};
const STATUS_TONE: Record<ConnectionStatus, 'neutral' | 'warning' | 'success' | 'danger' | 'info'> =
  {
    not_configured: 'neutral',
    unverified: 'warning',
    awaiting_approval: 'info',
    connected: 'success',
    failed: 'danger',
    revoked: 'danger',
  };
const ERROR_TEXT: Record<string, string> = {
  authentication_failed: 'The server refused the user name or password.',
  certificate_invalid: 'The server certificate could not be verified.',
  tls_failed: 'A secure connection could not be set up.',
  tls_not_supported: 'The server does not offer STARTTLS on this port.',
  timeout: 'The server did not answer in time.',
  connection_refused: 'The server refused the connection.',
  connection_failed: 'The server could not be reached.',
  host_not_public: 'This server is on a private network and cannot be used.',
  host_not_found: 'This host name could not be found.',
  send_not_permitted: 'This connection is not allowed to send email.',
  connection_revoked: 'The connection was revoked in MateMail.',
  sender_refused: 'The server refused the From address.',
  recipient_refused: 'The server refused the recipient.',
};

export function ConnectionBadge({ status }: { status: ConnectionStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_TEXT[status]}</Badge>;
}

function errorText(code: string): string {
  return code ? (ERROR_TEXT[code] ?? 'The last check did not succeed.') : '';
}

export function NotificationSettings({ path, session }: { path: string; session: StaffSession }) {
  const tab = (path.split('/')[2] ?? 'email') as Tab;
  const current = TABS.some((item) => item.key === tab) ? tab : 'email';
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Settings"
        title="Email and notifications"
        intro="How your store sends order and account messages, which texts customers receive and what happened to each message."
      />
      <nav aria-label="Notification settings" className="flex flex-wrap gap-1 border-b border-line">
        {TABS.map((item) => (
          <a
            key={item.key}
            href={`/manage/settings/notifications/${item.key}`}
            aria-current={item.key === current ? 'page' : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold ${
              item.key === current
                ? 'border-ink text-ink'
                : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {item.label}
          </a>
        ))}
      </nav>
      {current === 'email' && <EmailSettingsView session={session} />}
      {current === 'sms' && <SmsSettingsView />}
      {current === 'templates' && <TemplatesView />}
      {current === 'deliveries' && <DeliveriesView />}
    </div>
  );
}

// --- Email ----------------------------------------------------------------------------------------

type Pending = { title: string; description: string; run: () => Promise<unknown> } | null;

export function EmailSettingsView({ session }: { session: StaffSession }) {
  const load = useCallback(async () => emailSettings(await api.get<unknown>(`${BASE}/email/`)), []);
  const { data, setData, error, loading } = useLoad(load);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const [pending, setPending] = useState<Pending>(null);

  async function act(run: () => Promise<unknown>, success: string) {
    setActionError('');
    setNotice('');
    try {
      setData(emailSettings(await run()));
      setNotice(success);
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }

  function sensitive(title: string, description: string, run: () => Promise<unknown>) {
    setPending({ title, description, run });
  }

  if (loading && !data) return <Loader>Loading email settings…</Loader>;
  if (!data) return <Feedback error={error} />;
  return (
    <div className="grid gap-6">
      <Feedback error={actionError} notice={notice} />
      <ProviderChoice
        data={data}
        onSelect={(provider) =>
          sensitive(
            'Change the email provider',
            'All store email will be sent through the provider you choose. Confirm your password.',
            () => api.post<unknown>(`${BASE}/email/select/`, { provider }),
          )
        }
      />
      <SmtpCard
        connection={data.smtp}
        staffEmail={session.user.email ?? ''}
        onSave={(body) =>
          sensitive(
            'Save SMTP settings',
            'Changing mail server details needs your password. The connection must be tested again afterwards.',
            () => api.put<unknown>(`${BASE}/email/smtp/`, body),
          )
        }
        onAction={act}
      />
      <ImapCard
        connection={data.smtp}
        onSave={(body) =>
          sensitive('Save IMAP settings', 'Changing mailbox details needs your password.', () =>
            api.put<unknown>(`${BASE}/email/imap/`, body),
          )
        }
        onAction={act}
      />
      <MateMailCard
        available={data.matemail_available}
        connection={data.matemail}
        onStart={(body) =>
          sensitive('Connect MateMail', 'Connecting a mailbox needs your password.', () =>
            api.post<unknown>(`${BASE}/email/matemail/start/`, body),
          )
        }
        onDisconnect={() =>
          sensitive(
            'Disconnect MateMail',
            'Store email will stop going through MateMail. Confirm your password.',
            () => api.post<unknown>(`${BASE}/email/matemail/disconnect/`, {}),
          )
        }
        onAction={act}
      />
      <DeliverabilityCard />
      {pending && (
        <ReauthDialog
          title={pending.title}
          description={pending.description}
          onClose={() => setPending(null)}
          onConfirm={async () => {
            const run = pending.run;
            setPending(null);
            await act(run, 'Saved.');
          }}
        />
      )}
    </div>
  );
}

function ProviderChoice({
  data,
  onSelect,
}: {
  data: EmailSettings;
  onSelect: (provider: 'smtp' | 'matemail' | null) => void;
}) {
  const rows: {
    key: 'default' | 'smtp' | 'matemail';
    title: string;
    body: string;
    ready: boolean;
    status?: ConnectionStatus;
  }[] = [
    {
      key: 'default',
      title: 'MateCart default sender',
      body: data.default_sender_available
        ? 'Messages go out through the mail service NetaMate set up for this store.'
        : 'No default mail service is set up for this store, so email is not sent until you connect a provider.',
      ready: data.default_sender_available,
    },
    {
      key: 'smtp',
      title: 'Standard SMTP',
      body: 'Your own mail server or email provider.',
      ready: data.smtp.status === 'connected',
      status: data.smtp.status,
    },
    {
      key: 'matemail',
      title: 'MateMail Connected App',
      body: data.matemail_available
        ? 'Send from a MateMail mailbox you approved, without sharing its password.'
        : 'Not available for this store yet.',
      ready: data.matemail.status === 'connected',
      status: data.matemail.status,
    },
  ];
  const active = data.active_provider ?? 'default';
  return (
    <Card title="Email provider">
      <p className="mb-4 text-sm leading-6 text-muted">
        One provider sends all store email. MateCart never switches to another provider on its own,
        so a message is never sent twice.
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        {rows.map((row) => (
          <div
            key={row.key}
            className={`grid content-start gap-2 rounded-base border p-4 ${
              row.key === active ? 'border-ink' : 'border-line'
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-bold">{row.title}</p>
              {row.key === active ? (
                <Badge tone="success">In use</Badge>
              ) : (
                row.status && <ConnectionBadge status={row.status} />
              )}
            </div>
            <p className="text-xs leading-5 text-muted">{row.body}</p>
            {row.key !== active && (
              <div>
                <Button
                  variant="secondary"
                  disabled={!row.ready}
                  onClick={() => onSelect(row.key === 'default' ? null : row.key)}
                >
                  Use this provider
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

const SECURITY_OPTIONS: { value: MailSecurity; label: string }[] = [
  { value: 'starttls', label: 'STARTTLS (usually port 587)' },
  { value: 'tls', label: 'TLS (usually port 465)' },
  // The server accepts this only for the deployment's local test mail server (development
  // Mailpit); every real host is refused without TLS.
  { value: 'none', label: 'None (local test mail server only)' },
];

export function SmtpCard({
  connection,
  staffEmail,
  onSave,
  onAction,
}: {
  connection: MailConnectionState;
  staffEmail: string;
  onSave: (body: Record<string, unknown>) => void;
  onAction: (run: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    smtp_host: connection.smtp_host,
    smtp_port: String(connection.smtp_port ?? 587),
    smtp_security: connection.smtp_security === 'none' ? 'starttls' : connection.smtp_security,
    smtp_username: connection.smtp_username,
    from_email: connection.from_email,
    from_name: connection.from_name,
    reply_to: connection.reply_to,
  });
  // A new password only: the saved one is never sent to the browser.
  const [password, setPassword] = useState('');
  const [codeOpen, setCodeOpen] = useState(false);
  const set = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }));
  return (
    <Card title="Standard SMTP" actions={<ConnectionBadge status={connection.status} />}>
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          onSave({
            ...form,
            smtp_port: Number(form.smtp_port),
            ...(password ? { smtp_password: password } : {}),
          });
          setPassword('');
        }}
      >
        <div className="grid gap-4 md:grid-cols-3">
          <TextField
            label="SMTP server"
            required
            value={form.smtp_host}
            placeholder="smtp.example.com"
            onChange={(e) => set('smtp_host')(e.target.value)}
          />
          <TextField
            label="Port"
            inputMode="numeric"
            required
            value={form.smtp_port}
            onChange={(e) => set('smtp_port')(e.target.value)}
          />
          <SelectField
            label="Encryption"
            value={form.smtp_security}
            options={SECURITY_OPTIONS}
            onChange={(e) => set('smtp_security')(e.target.value)}
          />
          <TextField
            label="User name"
            autoComplete="off"
            value={form.smtp_username}
            onChange={(e) => set('smtp_username')(e.target.value)}
          />
          <PasswordField
            label={connection.smtp_password_set ? 'Replace password' : 'Password'}
            hint={
              connection.smtp_password_set
                ? 'Password saved. Leave empty to keep it.'
                : 'Use an app password if your provider offers one.'
            }
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <TextField
            label="From address"
            type="email"
            required
            value={form.from_email}
            onChange={(e) => set('from_email')(e.target.value)}
          />
          <TextField
            label="From name"
            value={form.from_name}
            onChange={(e) => set('from_name')(e.target.value)}
          />
          <TextField
            label="Reply-To address (optional)"
            type="email"
            value={form.reply_to}
            onChange={(e) => set('reply_to')(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="submit">Save SMTP settings</Button>
        </div>
      </form>
      {connection.smtp_host && (
        <div className="mt-5 grid gap-4 border-t border-line pt-5">
          <dl className="grid gap-3 sm:grid-cols-3">
            <Detail label="Connection test">
              {connection.last_verified_at
                ? `Passed ${dateTime(connection.last_verified_at)}`
                : errorText(connection.last_error) || 'Not tested yet'}
            </Detail>
            <Detail label="From address confirmed">
              {connection.sender_verified ? 'Yes' : 'Not yet'}
            </Detail>
            <Detail label="Password">
              {connection.smtp_password_set ? 'Password saved' : 'Not set'}
            </Detail>
          </dl>
          <p className="text-xs leading-5 text-muted">
            The connection becomes Connected after a successful test and after you confirm you own
            the From address with the code we email to it.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() =>
                void onAction(
                  () => api.post<unknown>(`${BASE}/email/smtp/test/`, { send_to_self: false }),
                  'Connection test finished.',
                )
              }
            >
              Test connection
            </Button>
            <Button
              variant="secondary"
              disabled={!staffEmail}
              onClick={() =>
                void onAction(
                  () => api.post<unknown>(`${BASE}/email/smtp/test/`, { send_to_self: true }),
                  `A test message was sent to ${staffEmail}.`,
                )
              }
            >
              Send a test to me
            </Button>
            {!connection.sender_verified && (
              <Button
                variant="secondary"
                onClick={() =>
                  void onAction(async () => {
                    const result = await api.post<unknown>(`${BASE}/email/smtp/sender-code/`, {});
                    setCodeOpen(true);
                    return result;
                  }, `A confirmation code was sent to ${connection.from_email}.`)
                }
              >
                Send confirmation code
              </Button>
            )}
            {connection.sender_code_pending && !connection.sender_verified && (
              <Button variant="secondary" onClick={() => setCodeOpen(true)}>
                Enter code
              </Button>
            )}
          </div>
        </div>
      )}
      {codeOpen && (
        <SenderCodeDialog
          address={connection.from_email}
          onClose={() => setCodeOpen(false)}
          onConfirm={async (code) => {
            await onAction(
              () => api.post<unknown>(`${BASE}/email/smtp/sender-confirm/`, { code }),
              'The From address is confirmed.',
            );
            setCodeOpen(false);
          }}
        />
      )}
    </Card>
  );
}

function SenderCodeDialog({
  address,
  onClose,
  onConfirm,
}: {
  address: string;
  onClose: () => void;
  onConfirm: (code: string) => Promise<void>;
}) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <Dialog title="Confirm the From address" onClose={onClose}>
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          void onConfirm(code).finally(() => setBusy(false));
        }}
      >
        <p className="text-sm leading-6 text-muted">
          Enter the six digit code we sent to {address}. It expires after 15 minutes.
        </p>
        <TextField
          label="Code"
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={busy}>
            Confirm
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ImapCard({
  connection,
  onSave,
  onAction,
}: {
  connection: MailConnectionState;
  onSave: (body: Record<string, unknown>) => void;
  onAction: (run: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    imap_host: connection.imap_host,
    imap_port: String(connection.imap_port ?? 993),
    imap_security: connection.imap_security === 'none' ? 'tls' : connection.imap_security,
    imap_username: connection.imap_username,
  });
  const [password, setPassword] = useState('');
  return (
    <Card title="IMAP mailbox" actions={<ConnectionBadge status={connection.imap_status} />}>
      <p className="mb-4 text-sm leading-6 text-muted">
        MateCart only checks that it can sign in to this mailbox, read only. It does not read, move
        or delete any message, and it is not an inbox for customer conversations.
      </p>
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          onSave({
            ...form,
            imap_port: form.imap_port ? Number(form.imap_port) : null,
            ...(password ? { imap_password: password } : {}),
          });
          setPassword('');
        }}
      >
        <div className="grid gap-4 md:grid-cols-4">
          <TextField
            label="IMAP server"
            value={form.imap_host}
            onChange={(e) => setForm({ ...form, imap_host: e.target.value })}
          />
          <TextField
            label="Port"
            inputMode="numeric"
            value={form.imap_port}
            onChange={(e) => setForm({ ...form, imap_port: e.target.value })}
          />
          <SelectField
            label="Encryption"
            value={form.imap_security}
            options={[
              { value: 'tls', label: 'TLS (usually port 993)' },
              { value: 'starttls', label: 'STARTTLS (usually port 143)' },
            ]}
            onChange={(e) =>
              setForm({ ...form, imap_security: e.target.value as 'tls' | 'starttls' })
            }
          />
          <TextField
            label="User name"
            autoComplete="off"
            value={form.imap_username}
            onChange={(e) => setForm({ ...form, imap_username: e.target.value })}
          />
          <PasswordField
            label={connection.imap_password_set ? 'Replace password' : 'Password'}
            hint={connection.imap_password_set ? 'Password saved. Leave empty to keep it.' : ''}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="submit">Save IMAP settings</Button>
          {connection.imap_host && (
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                void onAction(
                  () => api.post<unknown>(`${BASE}/email/imap/verify/`, {}),
                  'IMAP check finished.',
                )
              }
            >
              Verify sign in
            </Button>
          )}
        </div>
        {connection.imap_error && (
          <p className="text-sm text-danger-fg">{errorText(connection.imap_error)}</p>
        )}
      </form>
    </Card>
  );
}

export function MateMailCard({
  available,
  connection,
  onStart,
  onDisconnect,
  onAction,
}: {
  available: boolean;
  connection: MailConnectionState;
  onStart: (body: { tenant_id: string; integration_secret: string }) => void;
  onDisconnect: () => void;
  onAction: (run: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [tenant, setTenant] = useState('');
  const [secret, setSecret] = useState('');
  if (!available)
    return (
      <Card title="MateMail Connected App" actions={<Badge>Unavailable</Badge>}>
        <p className="text-sm leading-6 text-muted">
          MateMail is not available for this store yet. NetaMate turns it on for stores that use
          MateMail. Until then, use the default sender or Standard SMTP.
        </p>
      </Card>
    );
  return (
    <Card title="MateMail Connected App" actions={<ConnectionBadge status={connection.status} />}>
      {connection.status === 'connected' || connection.status === 'revoked' ? (
        <div className="grid gap-4">
          <dl className="grid gap-3 sm:grid-cols-3">
            <Detail label="Mailbox">{connection.matemail_mailbox || '—'}</Detail>
            <Detail label="Permissions">{connection.matemail_scopes.join(', ') || '—'}</Detail>
            <Detail label="Last check">
              {connection.last_verified_at ? dateTime(connection.last_verified_at) : '—'}
            </Detail>
          </dl>
          {connection.last_error && (
            <p className="text-sm text-danger-fg">{errorText(connection.last_error)}</p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() =>
                void onAction(
                  () => api.post<unknown>(`${BASE}/email/matemail/verify/`, {}),
                  'MateMail check finished.',
                )
              }
            >
              Verify connection
            </Button>
            <Button variant="danger" onClick={onDisconnect}>
              Disconnect
            </Button>
          </div>
        </div>
      ) : connection.status === 'awaiting_approval' && connection.matemail_authorization_url ? (
        <div className="grid gap-4">
          <p className="text-sm leading-6 text-muted">
            Open MateMail, sign in and approve this connection. Then come back and check.
          </p>
          <div className="flex flex-wrap gap-2">
            <a
              className="inline-flex items-center rounded-base border border-ink px-4 py-2 text-sm font-semibold"
              href={connection.matemail_authorization_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open MateMail to approve
            </a>
            <Button
              variant="secondary"
              onClick={() =>
                void onAction(
                  () => api.post<unknown>(`${BASE}/email/matemail/check/`, {}),
                  'Checked the approval.',
                )
              }
            >
              I approved it, check now
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            onStart({ tenant_id: tenant.trim(), integration_secret: secret });
            setSecret('');
          }}
        >
          <p className="text-sm leading-6 text-muted">
            In MateMail, open Settings, then Connected Apps. Create an app for one mailbox with
            permission to send email, then paste its Tenant ID and Integration Secret here. The
            secret is used once and never stored.
          </p>
          {connection.last_error && (
            <p className="text-sm text-danger-fg">{errorText(connection.last_error)}</p>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <TextField
              label="Tenant ID"
              required
              value={tenant}
              onChange={(e) => setTenant(e.target.value)}
            />
            <PasswordField
              label="Integration Secret"
              required
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </div>
          <div>
            <Button type="submit">Connect MateMail</Button>
          </div>
        </form>
      )}
    </Card>
  );
}

function DeliverabilityCard() {
  return (
    <Card title="Deliverability">
      <ul className="grid list-disc gap-1.5 pl-5 text-sm leading-6 text-muted">
        <li>
          Your domain&apos;s DNS needs SPF and DKIM records from your mail provider, and a DMARC
          policy. MateCart does not change DNS records.
        </li>
        <li>Send from an address on a domain your provider is allowed to send for.</li>
        <li>
          A provider accepting a message does not prove it reached the inbox. Delivery history shows
          what the provider accepted.
        </li>
      </ul>
    </Card>
  );
}

// --- SMS ------------------------------------------------------------------------------------------

const SENDER_TEXT: Record<string, string> = {
  none: 'No sender requested',
  pending: 'Waiting for approval',
  approved: 'Approved',
  rejected: 'Rejected',
};

function SmsSettingsView() {
  const load = useCallback(async () => smsSettings(await api.get<unknown>(`${BASE}/sms/`)), []);
  const { data, setData, error, loading } = useLoad(load);
  const [sender, setSender] = useState('');
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const [reauth, setReauth] = useState(false);
  async function patch(body: Record<string, unknown>, success: string) {
    setActionError('');
    setNotice('');
    try {
      setData(smsSettings(await api.patch<unknown>(`${BASE}/sms/`, body)));
      setNotice(success);
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }
  if (loading && !data) return <Loader>Loading SMS settings…</Loader>;
  if (!data) return <Feedback error={error} />;
  return (
    <div className="grid gap-6">
      <Feedback error={actionError} notice={notice} />
      {!data.provider_configured && (
        <Card title="SMS gateway">
          <p className="text-sm leading-6 text-muted">
            No SMS gateway is set up for this store yet. NetaMate connects the gateway; until then
            customers receive email and in-app updates only.
          </p>
        </Card>
      )}
      <Card title="Sender name" actions={<Badge>{SENDER_TEXT[data.sender_status]}</Badge>}>
        <p className="mb-4 text-sm leading-6 text-muted">
          Customers see this name as the sender. Mobile operators must approve it, so a new name is
          only a request: NetaMate registers it with the provider and turns it on when it is
          approved. Messages are never sent with an unapproved name.
        </p>
        <dl className="mb-4 grid gap-3 sm:grid-cols-2">
          <Detail label="Approved sender">{data.approved_sender || '—'}</Detail>
          <Detail label="Requested sender">{data.requested_sender || '—'}</Detail>
        </dl>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setReauth(true);
          }}
        >
          <div className="w-full max-w-xs">
            <TextField
              label="Request a sender name"
              hint="3 to 11 letters or digits."
              value={sender}
              maxLength={11}
              onChange={(e) => setSender(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary" disabled={!sender.trim()}>
            Request approval
          </Button>
        </form>
      </Card>
      <Card title="Text messages">
        <div className="grid gap-3">
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={data.is_enabled}
              disabled={data.sender_status !== 'approved' || !data.provider_configured}
              onChange={() => void patch({ is_enabled: !data.is_enabled }, 'Saved.')}
            />
            Send text messages to customers
            {data.sender_status !== 'approved' && (
              <span className="text-xs text-muted">(needs an approved sender name)</span>
            )}
          </label>
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={data.order_updates}
              onChange={() => void patch({ order_updates: !data.order_updates }, 'Saved.')}
            />
            Order confirmations
          </label>
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={data.shipping_updates}
              onChange={() => void patch({ shipping_updates: !data.shipping_updates }, 'Saved.')}
            />
            Shipping and delivery updates
          </label>
        </div>
      </Card>
      <Card title="Usage this month">
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Included" value={data.balance.allowance} hint="Message parts per month" />
          <Stat label="Used" value={data.balance.used} />
          <Stat label="Remaining" value={data.balance.remaining} />
          <Stat label="Prepaid credits" value={data.balance.prepaid_credits} />
        </div>
        <p className="mt-3 text-xs leading-5 text-muted">
          Long or Bangla messages use more than one part. Allowance and credits are arranged with
          NetaMate.
        </p>
      </Card>
      {reauth && (
        <ReauthDialog
          title="Request a sender name"
          description={`Request approval for "${sender.trim()}". Confirm your password.`}
          onClose={() => setReauth(false)}
          onConfirm={async () => {
            setReauth(false);
            await patch(
              { requested_sender: sender.trim() },
              'Requested. NetaMate will register it with the provider.',
            );
            setSender('');
          }}
        />
      )}
    </div>
  );
}

// --- Templates ------------------------------------------------------------------------------------

const AUDIENCE_LABEL: Record<string, string> = {
  customer: 'Customer messages',
  staff: 'Team alerts',
};

function TemplatesView() {
  const load = useCallback(
    async () => notificationTemplates(await api.get<unknown>(`${BASE}/templates/`)),
    [],
  );
  const { data, setData, error, loading } = useLoad(load);
  const [editing, setEditing] = useState<{ key: string; locale: 'en' | 'bn' } | null>(null);
  if (loading && !data) return <Loader>Loading templates…</Loader>;
  if (!data) return <Feedback error={error} />;
  const groups = ['customer', 'staff'].map((audience) => ({
    audience,
    rows: data.filter((row) => row.audience === audience),
  }));
  const current = editing ? data.find((row) => row.key === editing.key) : undefined;
  return (
    <div className="grid gap-6">
      <p className="text-sm leading-6 text-muted">
        Change the wording of each message in English and Bangla. Order numbers, amounts and links
        always come from your store records; codes and security wording cannot be changed.
      </p>
      {groups.map((group) => (
        <Card key={group.audience} title={AUDIENCE_LABEL[group.audience]} flush>
          <Table head={['Message', 'Type', 'English', 'Bangla', '']}>
            {group.rows.map((row) => (
              <tr key={row.key}>
                <Cell className="font-semibold">{row.title}</Cell>
                <Cell>{CATEGORY_LABEL[row.category]}</Cell>
                {row.locales.map((locale) => (
                  <Cell key={locale.locale}>
                    {!locale.is_enabled ? (
                      <Badge tone="warning">Off</Badge>
                    ) : locale.customized ? (
                      <Badge tone="info">Custom</Badge>
                    ) : (
                      <span className="text-xs text-muted">Default</span>
                    )}
                  </Cell>
                ))}
                <Cell>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => setEditing({ key: row.key, locale: 'en' })}
                    >
                      Edit English
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => setEditing({ key: row.key, locale: 'bn' })}
                    >
                      Edit Bangla
                    </Button>
                  </div>
                </Cell>
              </tr>
            ))}
          </Table>
        </Card>
      ))}
      {editing && current && (
        <TemplateEditor
          template={current}
          locale={editing.locale}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            setData(data.map((row) => (row.key === updated.key ? updated : row)));
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

const TEXT_FIELDS = [
  { key: 'subject', label: 'Email subject', defaultKey: 'default_subject', rows: 1 },
  { key: 'heading', label: 'Email heading', defaultKey: 'default_heading', rows: 1 },
  { key: 'body', label: 'Message', defaultKey: 'default_body', rows: 7 },
] as const;

export function TemplateEditor({
  template,
  locale,
  onClose,
  onSaved,
}: {
  template: NotificationTemplate;
  locale: 'en' | 'bn';
  onClose: () => void;
  onSaved: (template: NotificationTemplate) => void;
}) {
  const values = template.locales.find((row) => row.locale === locale) as TemplateLocale;
  const [form, setForm] = useState({
    subject: values.subject,
    heading: values.heading,
    body: values.body,
    sms: values.sms,
    in_app_title: values.in_app_title,
    in_app_body: values.in_app_body,
    is_enabled: values.is_enabled,
  });
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const url = `${BASE}/templates/${encodeURIComponent(template.key)}/${locale}/`;
  const { is_enabled, ...texts } = form;
  async function runPreview() {
    setError('');
    try {
      setPreview(templatePreview(await api.post<unknown>(`${url}preview/`, texts)));
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Dialog
      title={`${template.title} · ${locale === 'bn' ? 'Bangla' : 'English'}`}
      onClose={onClose}
    >
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          void api
            .put<unknown>(url, { ...texts, is_enabled })
            .then((v) => onSaved(notificationTemplate(v)))
            .catch((err: unknown) => setError(errorMessage(err)))
            .finally(() => setBusy(false));
        }}
      >
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs font-semibold text-muted">You can use:</span>
          {template.variables.map((name) => (
            <code key={name} className="rounded-badge bg-canvas px-1.5 py-0.5 text-xs">
              {`{${name}}`}
            </code>
          ))}
        </div>
        {TEXT_FIELDS.map((field) => (
          <div key={field.key} className="grid gap-1.5">
            <label className="text-[13px] font-semibold" htmlFor={`tpl-${field.key}`}>
              {field.label}
            </label>
            <textarea
              id={`tpl-${field.key}`}
              rows={field.rows}
              className="w-full rounded-base border border-line-strong bg-surface px-3 py-2 text-sm outline-none focus:border-brand-deep"
              placeholder={values[field.defaultKey]}
              value={form[field.key]}
              onChange={(e) => setForm({ ...form, [field.key]: e.target.value })}
            />
          </div>
        ))}
        {template.supports_sms && (
          <TextField
            label="Text message"
            placeholder={values.default_sms}
            value={form.sms}
            maxLength={480}
            onChange={(e) => setForm({ ...form, sms: e.target.value })}
          />
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <TextField
            label="In-app title (optional)"
            value={form.in_app_title}
            onChange={(e) => setForm({ ...form, in_app_title: e.target.value })}
          />
          <TextField
            label="In-app text (optional)"
            value={form.in_app_body}
            onChange={(e) => setForm({ ...form, in_app_body: e.target.value })}
          />
        </div>
        {template.category !== 'security' && (
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={form.is_enabled}
              onChange={() => setForm({ ...form, is_enabled: !form.is_enabled })}
            />
            Send this message
          </label>
        )}
        <p className="text-xs leading-5 text-muted">Empty fields use the default text shown.</p>
        {error && <p className="text-sm text-danger-fg">{error}</p>}
        <div className="flex flex-wrap justify-between gap-2">
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={() => void runPreview()}>
              Preview
            </Button>
            {values.customized && (
              <Button type="button" variant="secondary" onClick={() => setConfirmReset(true)}>
                Reset to default
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" busy={busy}>
              Save
            </Button>
          </div>
        </div>
        {preview && (
          <div className="grid gap-3 border-t border-line pt-4">
            <p className="text-sm">
              <span className="font-semibold">Subject:</span> {preview.subject}
            </p>
            <iframe
              title="Email preview"
              sandbox=""
              srcDoc={preview.html}
              className="h-[420px] w-full rounded-base border border-line bg-surface"
            />
            {preview.sms && (
              <p className="text-sm">
                <span className="font-semibold">Text message</span> ({preview.sms_segments}{' '}
                {preview.sms_segments === 1 ? 'part' : 'parts'}): {preview.sms}
              </p>
            )}
            <p className="text-sm">
              <span className="font-semibold">In app:</span> {preview.in_app_title} —{' '}
              {preview.in_app_body}
            </p>
          </div>
        )}
      </form>
      {confirmReset && (
        <ConfirmDialog
          title="Reset to default text?"
          confirmLabel="Reset"
          onCancel={() => setConfirmReset(false)}
          onConfirm={async () => {
            onSaved(notificationTemplate(await api.delete<unknown>(url)));
          }}
        >
          Your custom wording for this language will be removed.
        </ConfirmDialog>
      )}
    </Dialog>
  );
}

// --- Delivery history -----------------------------------------------------------------------------

const DELIVERY_STATUS: Record<string, string> = {
  pending: 'Waiting',
  queued: 'Queued',
  processing: 'Sending',
  sent: 'Accepted by provider',
  delivered: 'Delivery confirmed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  suppressed: 'Not sent',
};
const DELIVERY_TONE: Record<string, 'neutral' | 'warning' | 'success' | 'danger' | 'info'> = {
  pending: 'info',
  queued: 'info',
  processing: 'info',
  sent: 'success',
  delivered: 'success',
  failed: 'danger',
  cancelled: 'neutral',
  suppressed: 'warning',
};
const FAILURE_TEXT: Record<string, string> = {
  outcome_unknown: 'No answer from the provider; not resent automatically to avoid duplicates.',
  retries_exhausted: 'Temporary failures continued after all retries.',
  consent_withdrawn: 'The customer unsubscribed.',
  sms_unavailable: 'SMS is not set up.',
  sms_allowance_exhausted: 'The monthly SMS allowance is used up.',
  sms_rate_limited: 'Too many messages to this number.',
  no_email_transport: 'No email provider is set up.',
  connection_not_ready: 'The email provider is not connected.',
  ...ERROR_TEXT,
};
const CHANNEL_LABEL: Record<string, string> = { email: 'Email', sms: 'SMS', in_app: 'In app' };

function DeliveriesView() {
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('');
  const [channel, setChannel] = useState('');
  const load = useCallback(async () => {
    const [list, summary] = await Promise.all([
      api.get<unknown>(
        `${BASE}/deliveries/${query({ page, status: statusFilter || null, channel: channel || null })}`,
      ),
      api.get<unknown>(`${BASE}/deliveries/summary/`),
    ]);
    return { list: deliveryPage(list), summary: deliverySummary(summary) };
  }, [page, statusFilter, channel]);
  const { data, error, loading, reload } = useLoad(load);
  const [attempts, setAttempts] = useState<{
    row: NotificationDelivery;
    items: DeliveryAttemptRow[];
  } | null>(null);
  const [retry, setRetry] = useState<NotificationDelivery | null>(null);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  return (
    <div className="grid gap-6">
      <Feedback error={actionError || error} notice={notice} />
      {data && (
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Waiting or sending" value={data.summary.pending} />
          <Stat label="Accepted by provider" value={data.summary.sent} />
          <Stat label="Failed" value={data.summary.failed} />
          <Stat label="Not sent" value={data.summary.suppressed} hint="Preferences or limits" />
        </div>
      )}
      <Card flush>
        <Filters>
          <SelectField
            label="Status"
            value={statusFilter}
            onChange={(e) => {
              setPage(1);
              setStatusFilter(e.target.value);
            }}
            options={[
              { value: '', label: 'All' },
              ...Object.entries(DELIVERY_STATUS).map(([value, label]) => ({ value, label })),
            ]}
          />
          <SelectField
            label="Channel"
            value={channel}
            onChange={(e) => {
              setPage(1);
              setChannel(e.target.value);
            }}
            options={[
              { value: '', label: 'All' },
              { value: 'email', label: 'Email' },
              { value: 'sms', label: 'SMS' },
            ]}
          />
        </Filters>
        {loading && !data && <Loader>Loading delivery history…</Loader>}
        {data && data.list.results.length === 0 && (
          <Empty title="No messages yet">Order and account messages will appear here.</Empty>
        )}
        {data && data.list.results.length > 0 && (
          <Table head={['Sent', 'Message', 'To', 'Channel', 'Status', 'Attempts', '']}>
            {data.list.results.map((row) => (
              <tr key={row.id}>
                <Cell className="whitespace-nowrap">{dateTime(row.created_at)}</Cell>
                <Cell>
                  <p className="font-semibold">{row.subject || row.kind}</p>
                  <p className="text-xs text-muted">{row.kind}</p>
                </Cell>
                <Cell>{row.recipient}</Cell>
                <Cell>{CHANNEL_LABEL[row.channel]}</Cell>
                <Cell>
                  <Badge tone={DELIVERY_TONE[row.status]}>{DELIVERY_STATUS[row.status]}</Badge>
                  {row.failure_code && (
                    <p className="mt-1 max-w-[260px] text-xs text-muted">
                      {FAILURE_TEXT[row.failure_code] ?? row.failure_code}
                    </p>
                  )}
                </Cell>
                <Cell>{row.attempts}</Cell>
                <Cell>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      onClick={() =>
                        void api
                          .get<unknown>(`${BASE}/deliveries/${row.id}/attempts/`)
                          .then((v) => setAttempts({ row, items: deliveryAttempts(v) }))
                          .catch((e: unknown) => setActionError(errorMessage(e)))
                      }
                    >
                      Attempts
                    </Button>
                    {row.can_retry && (
                      <Button variant="secondary" onClick={() => setRetry(row)}>
                        Retry
                      </Button>
                    )}
                  </div>
                </Cell>
              </tr>
            ))}
          </Table>
        )}
        {data && (
          <Pagination
            page={data.list.page}
            pageSize={data.list.page_size}
            count={data.list.count}
            onPage={setPage}
          />
        )}
      </Card>
      {attempts && (
        <Dialog title="Delivery attempts" onClose={() => setAttempts(null)}>
          {attempts.items.length === 0 ? (
            <p className="text-sm text-muted">No attempts yet.</p>
          ) : (
            <ol className="grid gap-3">
              {attempts.items.map((item) => (
                <li key={item.number} className="rounded-base border border-line p-3 text-sm">
                  <p className="font-semibold">
                    Attempt {item.number} · {item.outcome} · {item.provider}
                  </p>
                  <p className="text-xs text-muted">{dateTime(item.occurred_at)}</p>
                  {item.error_code && (
                    <p className="mt-1 text-xs">
                      {FAILURE_TEXT[item.error_code] ?? item.error_code}
                    </p>
                  )}
                  {item.next_retry_at && (
                    <p className="text-xs text-muted">Next try {dateTime(item.next_retry_at)}</p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </Dialog>
      )}
      {retry && (
        <ConfirmDialog
          title="Send this message again?"
          confirmLabel="Send again"
          onCancel={() => setRetry(null)}
          onConfirm={async () => {
            try {
              notificationDelivery(
                await api.post<unknown>(`${BASE}/deliveries/${retry.id}/retry/`, {}),
              );
              setNotice('The message is queued to be sent again.');
              setRetry(null);
              await reload();
            } catch (e) {
              setActionError(errorMessage(e));
              setRetry(null);
            }
          }}
        >
          {retry.failure_code === 'outcome_unknown'
            ? 'The provider may already have accepted this message, so the recipient could receive it twice.'
            : 'It will be sent once more through the current email or SMS provider.'}
        </ConfirmDialog>
      )}
    </div>
  );
}
