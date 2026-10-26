'use client';

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { object, string } from '@matecart/shared-contracts';
import type { ApiClient } from './api';
import { errorMessage } from './api';
import {
  Alert,
  AuthCard,
  AuthShell,
  Button,
  Loading,
  PasswordField,
  TextField,
} from './auth-controls';
import { MfaChallengeForm, signInDestination } from './mfa-ui';
import { Wordmark } from './wordmark';

export type StaffRealm = 'staff' | 'console';
export function authPaths(realm: StaffRealm) {
  return realm === 'staff'
    ? {
        api: '/api/manage',
        login: '/manage/sign-in',
        home: '/manage',
        forgot: '/manage/forgot-password',
        reset: '/manage/reset-password',
        verify: '/manage/sign-in/verify',
        security: '/manage/account/security',
      }
    : {
        api: '/api/console',
        login: '/sign-in',
        home: '/',
        forgot: '/forgot-password',
        reset: '/reset-password',
        verify: '/sign-in/verify',
        security: '/account/security',
      };
}

export function EmployeeSignIn({
  api,
  realm,
  verify = false,
}: {
  api: ApiClient;
  realm: StaffRealm;
  verify?: boolean;
}) {
  const paths = authPaths(realm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  return (
    <AuthShell
      realm={realm}
      title={
        verify
          ? 'One more step to keep you safe.'
          : realm === 'staff'
            ? 'Welcome back to your business.'
            : 'Welcome to your workspace.'
      }
    >
      <AuthCard
        title={
          verify
            ? 'Verify it is you'
            : realm === 'staff'
              ? 'Sign in to manage your store'
              : 'Sign in to NetaMate Console'
        }
        description={
          verify
            ? 'Use a method you set up for this account.'
            : 'Enter the email and password for your team account.'
        }
      >
        {verify ? (
          <MfaChallengeForm
            api={api}
            base={`${paths.api}/auth`}
            onSuccess={() => window.location.assign(paths.home)}
          />
        ) : (
          <form
            className="grid gap-5"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError('');
              try {
                const result = await api.post<unknown>(`${paths.api}/auth/login/`, {
                  email,
                  password,
                });
                setPassword('');
                window.location.assign(signInDestination(result, paths.home, paths.verify));
              } catch (err) {
                setError(errorMessage(err));
                setPassword('');
              } finally {
                setBusy(false);
              }
            }}
          >
            <TextField
              label="Email address"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <PasswordField
              label="Password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <a
              href={paths.forgot}
              className="justify-self-end text-sm font-semibold text-brand-deep"
            >
              Forgot password?
            </a>
            {error && <Alert>{error}</Alert>}
            <Button type="submit" busy={busy} className="h-[50px]">
              Sign in
            </Button>
          </form>
        )}
        {verify ? (
          <a href={paths.login} className="text-center text-sm text-brand-deep">
            Start sign-in again
          </a>
        ) : (
          <p className="border-t border-line pt-4 text-xs leading-5 text-muted">
            {realm === 'staff'
              ? 'New to the team? Open the invitation from your store owner to activate your account.'
              : 'Accounts are provided by NetaMate. Two-step verification is required.'}
          </p>
        )}
      </AuthCard>
    </AuthShell>
  );
}

export function PasswordRecovery({
  api,
  base,
  realm = 'customer',
  reset = false,
  loginPath,
}: {
  api: ApiClient;
  base: string;
  realm?: 'customer' | StaffRealm;
  reset?: boolean;
  loginPath: string;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const token =
    typeof window === 'undefined'
      ? ''
      : (new URLSearchParams(window.location.search).get('token') ?? '');
  return (
    <AuthShell
      realm={realm}
      title={reset ? 'A fresh start for your password.' : 'Let’s get you back in.'}
    >
      <AuthCard
        title={
          done
            ? reset
              ? 'Password updated'
              : 'Check your inbox'
            : reset
              ? 'Choose a new password'
              : 'Forgot your password?'
        }
        description={
          done
            ? reset
              ? 'Your password is saved. Sign in again to continue.'
              : 'If this email has an account, we have sent a reset link. Check your spam folder too. The link expires in one hour.'
            : reset
              ? 'Use a unique password you do not use elsewhere.'
              : 'Enter your account email and we will help you reset your password.'
        }
      >
        {done ? (
          <a className="text-sm font-semibold text-brand-deep" href={loginPath}>
            Back to sign in →
          </a>
        ) : reset && !token ? (
          <Alert>This link is incomplete. Please request a new password reset email.</Alert>
        ) : (
          <form
            className="grid gap-5"
            onSubmit={async (e) => {
              e.preventDefault();
              setError('');
              if (reset && password !== repeat) {
                setError('Your passwords do not match.');
                return;
              }
              setBusy(true);
              try {
                await api.post(
                  `${base}/password/${reset ? 'reset' : 'forgot'}/`,
                  reset ? { token, password } : { email },
                );
                setPassword('');
                setRepeat('');
                setDone(true);
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            {reset ? (
              <>
                <PasswordField
                  label="New password"
                  hint={
                    realm === 'console'
                      ? 'Use at least 12 characters.'
                      : 'Use at least 10 characters.'
                  }
                  required
                  minLength={realm === 'console' ? 12 : 10}
                  maxLength={128}
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <PasswordField
                  label="Confirm new password"
                  autoComplete="new-password"
                  required
                  value={repeat}
                  onChange={(e) => setRepeat(e.target.value)}
                />
              </>
            ) : (
              <TextField
                label="Email address"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            )}
            {error && <Alert>{error}</Alert>}
            <Button type="submit" busy={busy}>
              {reset ? 'Save new password' : 'Send reset link'}
            </Button>
          </form>
        )}
        {!done && (
          <a href={loginPath} className="text-center text-sm text-brand-deep">
            Back to sign in
          </a>
        )}
      </AuthCard>
    </AuthShell>
  );
}

export function ActivationForm({ api, realm }: { api: ApiClient; realm: StaffRealm }) {
  const paths = authPaths(realm);
  const isConsole = realm === 'console';
  const base = isConsole ? '/api/console/auth/activate' : '/api/manage/invitation';
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [loadError, setLoadError] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    const token = new URLSearchParams(window.location.search).get('token') ?? '';
    if (!token) {
      queueMicrotask(() =>
        setLoadError('This invitation is incomplete. Ask for a new invitation email.'),
      );
      return;
    }
    void api
      .post<unknown>(`${base}/inspect/`, { token })
      .then((result) => {
        if (live) {
          const o = object(result);
          string(o.email);
          setPreview(o);
          setName(typeof o.full_name === 'string' ? o.full_name : '');
        }
      })
      .catch((err) => {
        if (live) setLoadError(errorMessage(err));
      });
    return () => {
      live = false;
    };
  }, [api, base]);
  return (
    <AuthShell
      realm={realm}
      title={isConsole ? 'Welcome to the NetaMate team.' : 'You’re invited. Let’s get you started.'}
    >
      <AuthCard
        title="Activate your account"
        description="Choose your own password. Your invitation works only once."
      >
        {loadError ? (
          <>
            <Alert>{loadError}</Alert>
            <a href={paths.login} className="text-sm text-brand-deep">
              Back to sign in
            </a>
          </>
        ) : !preview ? (
          <Loading>Checking your invitation…</Loading>
        ) : (
          <form
            className="grid gap-5"
            onSubmit={async (e) => {
              e.preventDefault();
              setError('');
              if (password !== repeat) {
                setError('Your passwords do not match.');
                return;
              }
              setBusy(true);
              try {
                const token = new URLSearchParams(window.location.search).get('token') ?? '';
                const result = await api.post<unknown>(isConsole ? `${base}/` : `${base}/accept/`, {
                  token,
                  password,
                  full_name: name,
                });
                setPassword('');
                setRepeat('');
                window.location.assign(signInDestination(result, paths.home, paths.verify));
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="rounded-base border border-line bg-canvas p-4 text-sm leading-6">
              <strong>{isConsole ? 'NetaMate Console' : string(preview.store_name)}</strong>
              <p>{string(preview.email)}</p>
              {!isConsole && (
                <p className="mt-2 text-muted">
                  {string(preview.role_label)} · {string(preview.role_description)}
                </p>
              )}
            </div>
            {!isConsole && (
              <TextField
                label="Your full name"
                required
                autoComplete="name"
                maxLength={150}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            )}
            <PasswordField
              label="Choose a password"
              hint={isConsole ? 'Use at least 12 characters.' : 'Use at least 10 characters.'}
              minLength={isConsole ? 12 : 10}
              maxLength={128}
              required
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <PasswordField
              label="Confirm password"
              required
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
            />
            {error && <Alert>{error}</Alert>}
            <Button type="submit" busy={busy}>
              Activate account
            </Button>
            <p className="text-xs leading-5 text-muted">
              {isConsole
                ? 'Next, you will set up two-step verification.'
                : 'Your shopping account stays separate from your team account.'}
            </p>
          </form>
        )}
      </AuthCard>
    </AuthShell>
  );
}

export function DashboardShell({
  children,
  realm,
  name,
  subtitle,
  team = false,
  links = [],
  onLogout,
}: {
  children: ReactNode;
  realm: StaffRealm;
  name: string;
  subtitle: string;
  team?: boolean;
  /** Extra sections the signed-in person may open (for example Appearance, Content). */
  links?: { href: string; label: string }[];
  onLogout: () => Promise<void>;
}) {
  const paths = authPaths(realm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <div className="min-h-dvh bg-canvas lg:pl-[248px]">
      <aside className="border-b border-line bg-surface lg:fixed lg:inset-y-0 lg:left-0 lg:w-[248px] lg:border-r lg:border-b-0">
        <a href={paths.home} className="flex h-16 items-center gap-3 border-b border-line px-6">
          <Wordmark className="text-2xl" />
          {realm === 'console' && <span className="text-xs text-muted">Console</span>}
        </a>
        <nav aria-label="Management" className="flex flex-wrap gap-1 p-4 lg:flex-col">
          <a
            className="rounded-base px-4 py-3 text-sm font-semibold hover:bg-canvas"
            href={paths.home}
          >
            Overview
          </a>
          {links.map((link) => (
            <a
              key={link.href}
              className="rounded-base px-4 py-3 text-sm font-semibold hover:bg-canvas"
              href={link.href}
            >
              {link.label}
            </a>
          ))}
          {team && (
            <a
              className="rounded-base px-4 py-3 text-sm font-semibold hover:bg-canvas"
              href="/manage/team"
            >
              Team & access
            </a>
          )}
          <a
            className="rounded-base px-4 py-3 text-sm font-semibold hover:bg-canvas"
            href={paths.security}
          >
            Account security
          </a>
          {realm === 'staff' && (
            <a className="px-4 py-3 text-sm text-muted" href="/">
              View store ↗
            </a>
          )}
        </nav>
      </aside>
      <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-6 py-3">
        <div>
          <p className="text-sm font-semibold">{name}</p>
          <p className="text-xs text-muted">{subtitle}</p>
        </div>
        <Button
          variant="secondary"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onLogout();
            } catch (err) {
              setError(errorMessage(err));
              setBusy(false);
            }
          }}
        >
          Sign out
        </Button>
      </header>
      <main className="mx-auto max-w-[1400px] p-5 sm:p-8">
        {error && (
          <div className="mb-5">
            <Alert>{error}</Alert>
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
