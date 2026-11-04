'use client';

import { useCallback, useEffect, useState } from 'react';
import type { StaffSession, Team } from '@matecart/shared-contracts';
import { staffSession, team } from '@matecart/shared-contracts';
import {
  ActivationForm,
  Alert,
  ApiError,
  Button,
  DashboardShell,
  Dialog,
  EmployeeSignIn,
  Loading,
  PasswordField,
  PasswordRecovery,
  SecuritySettings,
  TextField,
  errorMessage,
} from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';
import { navigation, requiredPermission } from '@/lib/manage-routes';
import { AppearanceSettings } from './appearance-app';
import { ContentSettings } from './content-app';
import { AfterSalesWorkspace } from './manage/aftersales';
import { AssistedOrderForm } from './manage/assisted-order';
import { CatalogManager } from './manage/catalog';
import { CustomerList, CustomerProfile } from './manage/customers';
import { InventoryManager } from './manage/inventory';
import { MarketingWorkspace } from './manage/marketing';
import { NotificationSettings } from './manage/notification-settings';
import { NotificationBell, NotificationCenter } from './manage/notifications';
import { OrderDetail, OrderInvoice, OrderList } from './manage/orders';
import { Overview } from './manage/overview';
import { ProductCreate, ProductEditor, ProductList } from './manage/products';
import { Reports } from './manage/reports';
import { SettingsHub } from './manage/settings';
import { ShipmentDetail, ShipmentList } from './manage/shipments';

function TeamSettings() {
  const [data, setData] = useState<Team | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [invite, setInvite] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('manager');
  const [pending, setPending] = useState<{
    path: string;
    title: string;
    body: Record<string, string>;
  } | null>(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      setData(team(await api.get<unknown>('/api/manage/team/')));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
  async function perform() {
    if (!pending) return;
    setBusy(true);
    setError('');
    try {
      await api.post('/api/manage/account/reauthenticate/', { password });
      setPassword('');
      await api.post(pending.path, pending.body);
      setSuccess('Your team settings have been updated.');
      setPending(null);
      setInvite(false);
      setEmail('');
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <div className="mb-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">
            Store management
          </p>
          <h1 className="mt-2 text-[28px] font-bold tracking-tight">Team & access</h1>
          <p className="mt-2 text-sm text-muted">
            Invite people you trust and choose what they can do.
          </p>
        </div>
        <Button onClick={() => setInvite(true)}>Invite staff</Button>
      </div>
      {success && (
        <div className="mb-5">
          <Alert tone="success">{success}</Alert>
        </div>
      )}
      {error && (
        <div className="mb-5">
          <Alert>{error}</Alert>
        </div>
      )}
      {!data ? (
        <Loading>Loading your team…</Loading>
      ) : (
        <>
          <section className="rounded-base border border-line bg-surface">
            <h2 className="border-b border-line px-5 py-4 text-base font-bold">
              Team members{' '}
              <span className="ml-2 text-sm font-normal text-muted">{data.members.length}</span>
            </h2>
            <ul className="divide-y divide-line">
              {data.members.map((member) => (
                <li
                  key={member.id}
                  className="flex flex-wrap items-center justify-between gap-4 p-5"
                >
                  <div className="flex items-center gap-3">
                    <span
                      aria-hidden="true"
                      className="flex h-10 w-10 items-center justify-center rounded-base bg-canvas font-semibold"
                    >
                      {(member.user.full_name || member.user.email || '?').charAt(0).toUpperCase()}
                    </span>
                    <div>
                      <p className="text-sm font-semibold">
                        {member.user.full_name || 'Team member'}
                      </p>
                      <p className="mt-1 text-xs text-muted">{member.user.email}</p>
                      <p className="mt-1 text-xs text-muted">
                        {member.status === 'active' ? 'Active' : 'Suspended'} · Two-step
                        verification {member.mfa_enabled ? 'on' : 'off'}
                      </p>
                    </div>
                  </div>
                  {member.role === 'owner' ? (
                    <span className="rounded-badge bg-canvas px-3 py-1 text-xs font-bold">
                      Owner
                    </span>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      <select
                        aria-label={`Role for ${member.user.full_name}`}
                        value={member.role}
                        className="h-11 rounded-base border border-line-strong bg-surface px-3 text-sm"
                        onChange={(e) => {
                          setPassword('');
                          setPending({
                            title: 'Change team role?',
                            path: `/api/manage/team/members/${member.id}/role/`,
                            body: { role: e.target.value },
                          });
                        }}
                      >
                        {data.roles.map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setPassword('');
                          setPending({
                            title:
                              member.status === 'active' ? 'Suspend access?' : 'Restore access?',
                            path: `/api/manage/team/members/${member.id}/${member.status === 'active' ? 'suspend' : 'reactivate'}/`,
                            body: {},
                          });
                        }}
                      >
                        {member.status === 'active' ? 'Suspend' : 'Restore'}
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
          <section className="mt-6 rounded-base border border-line bg-surface">
            <h2 className="border-b border-line px-5 py-4 text-base font-bold">
              Pending invitations
            </h2>
            {!data.invitations.length ? (
              <p className="p-6 text-sm text-muted">
                No invitations waiting. Invite a staff member when you are ready to grow your team.
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {data.invitations.map((i) => (
                  <li key={i.id} className="flex flex-wrap items-center justify-between gap-4 p-5">
                    <div>
                      <p className="text-sm font-semibold">{i.email}</p>
                      <p className="mt-1 text-xs text-muted">
                        {data.roles.find((r) => r.value === i.role)?.label ?? i.role} · Expires{' '}
                        {new Date(i.expires_at).toLocaleDateString()}
                      </p>
                      <span className="mt-2 inline-block rounded-badge bg-warning-bg px-2 py-1 text-xs font-semibold text-warning-fg">
                        Invitation sent
                      </span>
                    </div>
                    <div className="flex gap-2">
                      {['resend', 'revoke'].map((action) => (
                        <Button
                          key={action}
                          variant="secondary"
                          onClick={() => {
                            setPassword('');
                            setPending({
                              title:
                                action === 'resend'
                                  ? 'Send a new invitation?'
                                  : 'Revoke invitation?',
                              path: `/api/manage/team/invitations/${i.id}/${action}/`,
                              body: {},
                            });
                          }}
                        >
                          {action === 'resend' ? 'Resend' : 'Revoke'}
                        </Button>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
      {invite && !pending && (
        <Dialog title="Invite a team member" onClose={() => setInvite(false)}>
          <form
            className="grid gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              setPassword('');
              setPending({
                title: 'Send team invitation?',
                path: '/api/manage/team/invitations/',
                body: { email, role },
              });
            }}
          >
            <TextField
              label="Staff email address"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              hint="We will send a private invitation. They choose their own password."
            />
            <fieldset className="grid gap-3">
              <legend className="mb-3 text-sm font-semibold">Choose a role</legend>
              {data?.roles.map((r) => (
                <label
                  key={r.value}
                  className={`flex cursor-pointer gap-3 rounded-base border p-4 ${role === r.value ? 'border-ink' : 'border-line'}`}
                >
                  <input
                    type="radio"
                    name="role"
                    value={r.value}
                    checked={role === r.value}
                    onChange={() => setRole(r.value)}
                    className="mt-1 accent-brand-deep"
                  />
                  <span>
                    <strong className="text-sm">{r.label}</strong>
                    <span className="mt-1 block text-xs leading-5 text-muted">{r.description}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <Button type="submit">Continue</Button>
          </form>
        </Dialog>
      )}
      {pending && (
        <Dialog
          title={pending.title}
          onClose={() => {
            setPending(null);
            setPassword('');
          }}
        >
          <form
            className="grid gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              void perform();
            }}
          >
            <p className="text-sm leading-6 text-muted">
              Confirm with your password. Access changes apply immediately and may sign the staff
              member out.
            </p>
            <PasswordField
              label="Your password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && <Alert>{error}</Alert>}
            <div className="flex justify-end gap-3">
              <Button variant="secondary" type="button" onClick={() => setPending(null)}>
                Cancel
              </Button>
              <Button type="submit" busy={busy}>
                Confirm
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </div>
  );
}

function Section({ path, session }: { path: string; session: StaffSession }) {
  const can = (permission: string) => session.permissions.includes(permission);
  const [first = '', second = '', third = ''] = path.split('/');
  if (path === '') return <Overview session={session} />;
  switch (first) {
    case 'orders':
      if (second === 'new') return <AssistedOrderForm session={session} />;
      if (third === 'invoice') return <OrderInvoice id={second} />;
      return second ? <OrderDetail id={second} session={session} /> : <OrderList />;
    case 'shipments':
      return second ? <ShipmentDetail id={second} session={session} /> : <ShipmentList />;
    case 'products':
      if (second === 'new') return <ProductCreate session={session} />;
      return second ? (
        <ProductEditor id={second} session={session} />
      ) : (
        <ProductList session={session} />
      );
    case 'catalog':
      return <CatalogManager />;
    case 'inventory':
      return <InventoryManager />;
    case 'customers':
      return second ? <CustomerProfile id={second} /> : <CustomerList />;
    case 'aftersales':
      return <AfterSalesWorkspace />;
    case 'reports':
      return <Reports />;
    case 'settings':
      if (second === 'notifications') return <NotificationSettings path={path} session={session} />;
      return <SettingsHub session={session} />;
    case 'notifications':
      return <NotificationCenter />;
    case 'appearance':
      return <AppearanceSettings canManageSettings={can('store.settings')} />;
    case 'content':
      return <ContentSettings />;
    case 'team':
      return <TeamSettings />;
    case 'marketing':
      return <MarketingWorkspace path={path} session={session} />;
    default:
      return <SecuritySettings api={api} base="/api/manage/account" />;
  }
}

function ProtectedManagement({ path }: { path: string }) {
  const [session, setSession] = useState<StaffSession | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    void api
      .get<unknown>('/api/manage/auth/session/')
      .then((v) => {
        if (live) setSession(staffSession(v));
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) window.location.replace('/manage/sign-in');
        else if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, []);
  if (!session)
    return error ? (
      <div className="p-8">
        <Alert>{error}</Alert>
        <a href="/manage/sign-in" className="text-sm text-brand-deep">
          Back to sign in
        </a>
      </div>
    ) : (
      <Loading>Checking your team access…</Loading>
    );
  const required = requiredPermission(path);
  const allowed = !required || session.permissions.includes(required);
  return (
    <DashboardShell
      realm="staff"
      name={session.store_name}
      subtitle={`${session.user.full_name} · ${session.role_label}`}
      groups={navigation(session.permissions)}
      current={`/manage${path ? `/${path}` : ''}`}
      headerActions={<NotificationBell />}
      onLogout={async () => {
        await api.post('/api/manage/auth/logout/');
        window.location.assign('/manage/sign-in');
      }}
    >
      {allowed ? (
        <Section path={path} session={session} />
      ) : (
        <Alert>You do not have permission to open this page. Ask your store owner for help.</Alert>
      )}
    </DashboardShell>
  );
}

export function ManageApp({ path }: { path: string }) {
  if (path === 'sign-in' || path === 'sign-in/verify')
    return <EmployeeSignIn api={api} realm="staff" verify={path.endsWith('/verify')} />;
  if (path === 'forgot-password' || path === 'reset-password')
    return (
      <PasswordRecovery
        api={api}
        realm="staff"
        base="/api/manage/auth"
        reset={path === 'reset-password'}
        loginPath="/manage/sign-in"
      />
    );
  if (path === 'invitation') return <ActivationForm api={api} realm="staff" />;
  return <ProtectedManagement path={path} />;
}
