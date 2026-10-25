'use client';

import { useEffect, useState } from 'react';
import type { UserSummary } from '@matecart/shared-contracts';
import { userSummary } from '@matecart/shared-contracts';
import {
  ActivationForm,
  Alert,
  ApiError,
  AuthCard,
  AuthShell,
  Button,
  DashboardShell,
  Dialog,
  EmailSetup,
  EmployeeSignIn,
  Loading,
  PasswordRecovery,
  SecuritySettings,
  TotpSetupWizard,
  beginTotp,
  confirmTotp,
  errorMessage,
} from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';

function Enrollment() {
  const [mode, setMode] = useState<'totp' | 'email' | null>(null);
  const [error, setError] = useState('');
  return (
    <AuthShell realm="console" title="Let’s secure your console account.">
      <AuthCard
        title="Set up two-step verification"
        description="Before opening the console, add a second way to prove it is you. It only takes a few minutes."
      >
        {error && <Alert>{error}</Alert>}
        <div className="grid gap-3">
          <Button variant="secondary" onClick={() => setMode('totp')}>
            Use an authenticator app
          </Button>
          <p className="text-xs leading-5 text-muted">
            Recommended. Codes are available on your phone, even offline.
          </p>
          <Button variant="secondary" onClick={() => setMode('email')}>
            Use email codes
          </Button>
          <p className="text-xs leading-5 text-muted">
            Receive a code at your verified email each time you sign in.
          </p>
        </div>
        <button
          type="button"
          className="text-sm text-muted"
          onClick={async () => {
            try {
              await api.post('/api/console/auth/logout/');
              window.location.assign('/sign-in');
            } catch (e) {
              setError(errorMessage(e));
            }
          }}
        >
          Sign out and finish later
        </button>
        {mode && (
          <Dialog
            title={mode === 'totp' ? 'Set up an authenticator app' : 'Turn on email codes'}
            onClose={() => setMode(null)}
          >
            {mode === 'totp' ? (
              <TotpSetupWizard
                begin={() => beginTotp(api, '/api/console/enroll')}
                confirm={(code) => confirmTotp(api, '/api/console/enroll', code)}
                onDone={() => window.location.assign('/')}
              />
            ) : (
              <EmailSetup
                api={api}
                base="/api/console/enroll"
                onDone={() => window.location.assign('/')}
              />
            )}
          </Dialog>
        )}
      </AuthCard>
    </AuthShell>
  );
}

function ProtectedConsole({ security }: { security: boolean }) {
  const [user, setUser] = useState<UserSummary | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    void api
      .get<unknown>('/api/console/auth/session/')
      .then((v) => {
        if (live) setUser(userSummary(v));
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) window.location.replace('/sign-in');
        else if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, []);
  if (!user)
    return error ? (
      <div className="p-8">
        <Alert>{error}</Alert>
        <a href="/sign-in" className="text-sm text-brand-deep">
          Back to sign in
        </a>
      </div>
    ) : (
      <Loading>Checking your console access…</Loading>
    );
  return (
    <DashboardShell
      realm="console"
      name="NetaMate Console"
      subtitle={user.full_name || user.email || 'Administrator'}
      onLogout={async () => {
        await api.post('/api/console/auth/logout/');
        window.location.assign('/sign-in');
      }}
    >
      {security ? (
        <SecuritySettings api={api} base="/api/console/account" mandatory />
      ) : (
        <div className="max-w-3xl">
          <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">
            NetaMate operations
          </p>
          <h1 className="mt-2 text-[28px] font-bold tracking-tight">
            Welcome, {user.full_name || 'administrator'}.
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted">
            Your account is protected with two-step verification.
          </p>
          <a
            href="/account/security"
            className="mt-8 block rounded-base border border-line bg-surface p-6 hover:border-ink"
          >
            <h2 className="text-lg font-bold">Account security →</h2>
            <p className="mt-3 text-sm leading-6 text-muted">
              Manage your sign-in methods and recovery codes.
            </p>
          </a>
        </div>
      )}
    </DashboardShell>
  );
}

export function ConsoleApp({ path }: { path: string }) {
  if (path === 'sign-in' || path === 'sign-in/verify')
    return <EmployeeSignIn api={api} realm="console" verify={path.endsWith('/verify')} />;
  if (path === 'activate') return <ActivationForm api={api} realm="console" />;
  if (path === 'enroll') return <Enrollment />;
  if (path === 'forgot-password' || path === 'reset-password')
    return (
      <PasswordRecovery
        api={api}
        realm="console"
        base="/api/console/auth"
        reset={path === 'reset-password'}
        loginPath="/sign-in"
      />
    );
  return <ProtectedConsole security={path === 'account/security'} />;
}
