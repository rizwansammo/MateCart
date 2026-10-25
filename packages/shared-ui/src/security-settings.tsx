'use client';

import { useCallback, useEffect, useState } from 'react';
import type { SecurityStatus } from '@matecart/shared-contracts';
import { recoveryCodes, securityStatus } from '@matecart/shared-contracts';
import type { ApiClient } from './api';
import { errorMessage } from './api';
import { Alert, Button, Dialog, Loading, PasswordField } from './auth-controls';
import { EmailSetup, RecoveryCodesPanel, TotpSetupWizard, beginTotp, confirmTotp } from './mfa-ui';

type Action = 'totp' | 'email' | 'disable_totp' | 'disable_email' | 'regenerate' | 'password';

export function SecuritySettings({
  api,
  base,
  mandatory = false,
}: {
  api: ApiClient;
  base: string;
  mandatory?: boolean;
}) {
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [action, setAction] = useState<Action | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const load = useCallback(async () => {
    try {
      setStatus(securityStatus(await api.get<unknown>(`${base}/security/`)));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [api, base]);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
  function close() {
    setAction(null);
    setConfirmed(false);
    setPassword('');
    setNewPassword('');
    setCodes(null);
    void load();
  }
  function choose(value: Action) {
    setError('');
    setMessage('');
    setConfirmed(false);
    setAction(value);
  }
  async function authorize() {
    setBusy(true);
    setError('');
    try {
      await api.post(`${base}/reauthenticate/`, { password });
      if (action !== 'password') setPassword('');
      setConfirmed(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function change() {
    setBusy(true);
    setError('');
    try {
      if (action === 'regenerate') {
        setCodes(recoveryCodes(await api.post<unknown>(`${base}/mfa/recovery-codes/`)));
      } else if (action === 'password') {
        await api.post(`${base}/password/change/`, {
          current_password: password,
          new_password: newPassword,
        });
        close();
        setMessage('Password updated. Other signed-in devices have been signed out.');
      } else {
        await api.post(`${base}/mfa/${action === 'disable_totp' ? 'totp' : 'email'}/disable/`);
        close();
        setMessage('Your security settings have been updated.');
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const titles: Record<Action, string> = {
    totp: 'Set up an authenticator app',
    email: 'Turn on email codes',
    disable_totp: 'Remove authenticator app?',
    disable_email: 'Turn off email codes?',
    regenerate: 'Replace recovery codes?',
    password: 'Change your password',
  };
  if (!status)
    return error ? <Alert>{error}</Alert> : <Loading>Loading security settings…</Loading>;
  const enabled = status.totp || status.email_codes;
  return (
    <div className="max-w-3xl">
      <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">Your account</p>
      <h1 className="mt-2 text-[28px] font-bold tracking-tight">Account security</h1>
      <p className="mt-3 mb-7 text-sm leading-6 text-muted">
        Manage how you sign in and keep your account safe.
      </p>
      {message && (
        <div className="mb-5">
          <Alert tone="success">{message}</Alert>
        </div>
      )}
      {error && !action && (
        <div className="mb-5">
          <Alert>{error}</Alert>
        </div>
      )}
      <section className="mb-5 rounded-base border border-line bg-surface p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-bold">Two-step verification</h2>
          <span
            className={`rounded-badge px-3 py-1 text-xs font-bold ${enabled ? 'bg-success-bg text-success-fg' : 'bg-canvas text-muted'}`}
          >
            {enabled ? 'On' : 'Not enabled'}
          </span>
        </div>
        <p className="mt-3 text-sm leading-6 text-muted">
          {mandatory
            ? 'Two-step verification is required for NetaMate administrators. Keep at least one method enabled.'
            : 'Add an extra check after your password. It is optional, and helps protect your store.'}
        </p>
        <div className="mt-5 divide-y divide-line">
          <div className="flex flex-wrap items-center justify-between gap-4 py-5">
            <div>
              <h3 className="text-sm font-semibold">Authenticator app</h3>
              <p className="mt-1 text-sm text-muted">
                {status.totp
                  ? 'Connected. Codes work even without a signal.'
                  : 'Get sign-in codes from an app on your phone.'}
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => choose('totp')}>
                {status.totp ? 'Replace app' : 'Set up app'}
              </Button>
              {status.totp && (
                <Button
                  variant="secondary"
                  disabled={mandatory && !status.email_codes}
                  onClick={() => choose('disable_totp')}
                >
                  Remove
                </Button>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-4 py-5">
            <div>
              <h3 className="text-sm font-semibold">Email codes</h3>
              <p className="mt-1 text-sm text-muted">
                {status.email_codes
                  ? `On · ${status.email ?? ''}`
                  : 'Receive a code at your verified email address.'}
              </p>
            </div>
            <Button
              variant="secondary"
              disabled={mandatory && status.email_codes && !status.totp}
              onClick={() => choose(status.email_codes ? 'disable_email' : 'email')}
            >
              {status.email_codes ? 'Turn off' : 'Turn on'}
            </Button>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-4 py-5">
            <div>
              <h3 className="text-sm font-semibold">Recovery codes</h3>
              <p className="mt-1 text-sm text-muted">
                {enabled
                  ? `${status.recovery_codes_remaining} unused codes. Keep them in a safe place.`
                  : 'Available after you turn on two-step verification.'}
              </p>
            </div>
            <Button variant="secondary" disabled={!enabled} onClick={() => choose('regenerate')}>
              Get new codes
            </Button>
          </div>
        </div>
        <p className="border-t border-line pt-4 text-xs leading-6 text-muted">
          Cannot access your usual method? Choose a recovery code when signing in. If you have none
          left, contact your store owner or NetaMate support. We never ask you to share a
          verification code.
        </p>
      </section>
      <section className="flex flex-wrap items-center justify-between gap-4 rounded-base border border-line bg-surface p-6">
        <div>
          <h2 className="text-lg font-bold">Password</h2>
          <p className="mt-2 text-sm text-muted">
            Choose a unique password you do not use anywhere else.
          </p>
        </div>
        <Button variant="secondary" onClick={() => choose('password')}>
          Change password
        </Button>
      </section>
      {action && (
        <Dialog title={titles[action]} onClose={close}>
          {error && (
            <div className="mb-4">
              <Alert>{error}</Alert>
            </div>
          )}
          {!confirmed ? (
            <form
              className="grid gap-5"
              onSubmit={(e) => {
                e.preventDefault();
                void authorize();
              }}
            >
              <p className="text-sm leading-6 text-muted">
                For your safety, enter your current password before changing your sign-in settings.
              </p>
              <PasswordField
                label="Current password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <Button type="submit" busy={busy}>
                Confirm it is me
              </Button>
            </form>
          ) : codes ? (
            <RecoveryCodesPanel codes={codes} onDone={close} />
          ) : action === 'totp' ? (
            <TotpSetupWizard
              begin={() => beginTotp(api, `${base}/mfa`)}
              confirm={(code) => confirmTotp(api, `${base}/mfa`, code)}
              onDone={() => {
                close();
                setMessage('Your authenticator app is ready.');
              }}
            />
          ) : action === 'email' ? (
            <EmailSetup
              api={api}
              base={`${base}/mfa`}
              onDone={() => {
                close();
                setMessage('Email verification is ready.');
              }}
            />
          ) : action === 'password' ? (
            <form
              className="grid gap-5"
              onSubmit={(e) => {
                e.preventDefault();
                void change();
              }}
            >
              <PasswordField
                label="Current password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <PasswordField
                label="New password"
                autoComplete="new-password"
                hint={mandatory ? 'At least 12 characters.' : 'At least 10 characters.'}
                minLength={mandatory ? 12 : 10}
                maxLength={128}
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
              <Button type="submit" busy={busy}>
                Save new password
              </Button>
            </form>
          ) : (
            <div className="grid gap-5">
              <p className="text-sm leading-6 text-muted">
                {action === 'regenerate'
                  ? 'All previous recovery codes will stop working. Save the new codes before closing this window.'
                  : 'You will no longer be able to use this method to sign in. Make sure you can access your other sign-in method.'}
              </p>
              <div className="flex justify-end gap-3">
                <Button variant="secondary" onClick={close}>
                  Cancel
                </Button>
                <Button
                  variant={action === 'regenerate' ? 'primary' : 'danger'}
                  busy={busy}
                  onClick={() => void change()}
                >
                  {action === 'regenerate' ? 'Generate new codes' : 'Confirm removal'}
                </Button>
              </div>
            </div>
          )}
        </Dialog>
      )}
    </div>
  );
}
