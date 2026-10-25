'use client';

import { useEffect, useState } from 'react';
import type { Challenge, Customer, Methods } from '@matecart/shared-contracts';
import { challenge, customer, methods, object, string } from '@matecart/shared-contracts';
import {
  Alert,
  ApiError,
  AuthCard,
  AuthShell,
  Button,
  Dialog,
  Loading,
  OTPInput,
  PasswordField,
  PasswordRecovery,
  PhoneField,
  ResendCode,
  TextField,
  errorMessage,
} from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';

function useMethods() {
  const [value, setValue] = useState<Methods | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    void api
      .get<unknown>('/api/account/methods/')
      .then((v) => {
        if (live) setValue(methods(v));
      })
      .catch((e) => {
        if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, []);
  return { value, error };
}

function Verification({
  sent,
  onVerify,
  onResend,
  title = 'Check your inbox',
}: {
  sent: Challenge;
  onVerify: (code: string) => Promise<void>;
  onResend: () => Promise<void>;
  title?: string;
}) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <form
      className="grid gap-5"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
          await onVerify(code);
        } catch (err) {
          setError(errorMessage(err));
          setCode('');
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3 className="text-lg font-bold">{title}</h3>
      <p className="text-sm leading-6 text-muted">
        Enter the code we sent to <strong>{sent.destination}</strong>. It expires in 10 minutes.
      </p>
      <OTPInput value={code} onChange={setCode} autoFocus />
      {error && <Alert>{error}</Alert>}
      <Button type="submit" busy={busy} disabled={code.length !== 6}>
        Verify and continue
      </Button>
      <ResendCode seconds={sent.resend_in} onResend={onResend} />
    </form>
  );
}

function CustomerSignIn({ register }: { register: boolean }) {
  const available = useMethods();
  const [method, setMethod] = useState('email');
  const [sent, setSent] = useState<Challenge | null>(null);
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [completion, setCompletion] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const queryError =
    typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('error');
  const googleError =
    queryError === 'google_email_in_use' ||
    queryError === 'google_account_exists' ||
    queryError === 'account_exists'
      ? 'An account already uses this email. Sign in first, then link Google from your account.'
      : queryError
        ? 'Google sign-in could not be completed. Please try again or use your email.'
        : '';
  async function submit() {
    setError('');
    setBusy(true);
    try {
      if (completion && sent) {
        await api.post('/api/account/phone/complete/', {
          challenge_id: sent.challenge_id,
          completion_token: completion,
          full_name: name,
        });
        window.location.assign('/account');
      } else if (method === 'phone')
        setSent(challenge(await api.post<unknown>('/api/account/phone/start/', { phone })));
      else if (register) {
        setSent(
          challenge(
            await api.post<unknown>('/api/account/register/', { email, password, full_name: name }),
          ),
        );
        setPassword('');
      } else {
        customer(await api.post<unknown>('/api/account/login/', { email, password }));
        setPassword('');
        window.location.assign('/account');
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthShell
      title={
        register ? 'A little closer to what you love.' : 'Welcome back. Make yourself at home.'
      }
    >
      <AuthCard
        title={
          sent
            ? completion
              ? 'One last detail'
              : 'Verify your account'
            : register
              ? 'Create your account'
              : 'Sign in'
        }
        description={sent ? undefined : 'Choose a sign-in method that works for you.'}
      >
        {!sent && (
          <>
            <div className="grid grid-cols-2 gap-1 rounded-base bg-canvas p-1">
              <a
                href="/account/sign-in"
                aria-current={!register ? 'page' : undefined}
                className={`rounded-base p-2.5 text-center text-sm font-semibold ${!register ? 'bg-surface shadow-card' : 'text-muted'}`}
              >
                Sign in
              </a>
              <a
                href="/account/register"
                aria-current={register ? 'page' : undefined}
                className={`rounded-base p-2.5 text-center text-sm font-semibold ${register ? 'bg-surface shadow-card' : 'text-muted'}`}
              >
                Create account
              </a>
            </div>
            <div className="flex gap-6 border-b border-line">
              <button
                type="button"
                aria-pressed={method === 'email'}
                className={`min-h-11 border-b-2 text-sm font-semibold ${method === 'email' ? 'border-ink' : 'border-transparent text-muted'}`}
                onClick={() => {
                  setMethod('email');
                  setError('');
                }}
              >
                Email & password
              </button>
              <button
                type="button"
                aria-pressed={method === 'phone'}
                disabled={!available.value?.phone}
                className={`min-h-11 border-b-2 text-sm font-semibold disabled:opacity-50 ${method === 'phone' ? 'border-ink' : 'border-transparent text-muted'}`}
                onClick={() => {
                  setMethod('phone');
                  setError('');
                }}
              >
                Phone code
              </button>
            </div>
            {available.value && !available.value.phone && (
              <p className="text-xs text-muted">
                Text-message sign-in is currently unavailable. Use your email instead.
              </p>
            )}
            {!available.value && (
              <p role="status" className="text-xs text-muted">
                {available.error || 'Checking sign-in options…'}
              </p>
            )}
          </>
        )}
        {sent && !completion ? (
          <>
            <Verification
              sent={sent}
              title={method === 'phone' ? 'Check your messages' : 'Check your inbox'}
              onResend={async () =>
                setSent(
                  challenge(
                    await api.post<unknown>('/api/account/code/resend/', {
                      challenge_id: sent.challenge_id,
                    }),
                  ),
                )
              }
              onVerify={async (code) => {
                if (method === 'email') {
                  customer(
                    await api.post<unknown>('/api/account/register/verify/', {
                      challenge_id: sent.challenge_id,
                      code,
                    }),
                  );
                  window.location.assign('/account');
                } else {
                  const o = object(
                    await api.post<unknown>('/api/account/phone/verify/', {
                      challenge_id: sent.challenge_id,
                      code,
                    }),
                  );
                  const state = string(o.status);
                  if (state === 'needs_name') setCompletion(string(o.completion_token));
                  else if (state === 'signed_in') window.location.assign('/account');
                  else throw new Error('Unexpected sign-in state');
                }
              }}
            />
            <button
              className="text-sm text-brand-deep"
              onClick={() => {
                setSent(null);
                setCompletion('');
              }}
            >
              Use a different {method === 'phone' ? 'number' : 'email'}
            </button>
          </>
        ) : (
          <form
            className="grid gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            {((register && method === 'email') || completion) && (
              <TextField
                label="Full name"
                required
                autoComplete="name"
                maxLength={150}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            )}
            {!completion &&
              (method === 'phone' ? (
                <PhoneField
                  label="Mobile phone number"
                  required
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              ) : (
                <>
                  <TextField
                    label="Email address"
                    type="email"
                    autoComplete={register ? 'email' : 'username'}
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                  <PasswordField
                    label="Password"
                    autoComplete={register ? 'new-password' : 'current-password'}
                    minLength={register ? 10 : undefined}
                    maxLength={128}
                    hint={register ? 'Use at least 10 characters.' : undefined}
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  {!register && (
                    <a
                      href="/account/forgot-password"
                      className="justify-self-end text-sm font-semibold text-brand-deep"
                    >
                      Forgot password?
                    </a>
                  )}
                </>
              ))}
            {(error || googleError) && <Alert>{error || googleError}</Alert>}
            <Button type="submit" className="h-[50px]" busy={busy}>
              {completion
                ? 'Finish creating account'
                : method === 'phone'
                  ? 'Send verification code'
                  : register
                    ? 'Create account'
                    : 'Sign in'}
            </Button>
          </form>
        )}
        {!sent && (
          <>
            <div className="flex items-center gap-3 text-xs text-muted">
              <span className="h-px flex-1 bg-line" />
              or
              <span className="h-px flex-1 bg-line" />
            </div>
            {available.value?.google ? (
              <a
                href="/api/account/google/start/"
                className="flex h-12 items-center justify-center gap-3 rounded-base border border-line-strong text-sm font-semibold hover:border-ink"
              >
                <span className="text-lg" aria-hidden="true">
                  G
                </span>
                Continue with Google
              </a>
            ) : (
              <Button variant="secondary" disabled>
                Google sign-in unavailable
              </Button>
            )}
            <p className="text-xs leading-5 text-muted">
              Your details are used for your account. You choose delivery information separately
              when placing an order.
            </p>
          </>
        )}
      </AuthCard>
    </AuthShell>
  );
}

function Account() {
  const [user, setUser] = useState<Customer | null>(null);
  const available = useMethods();
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [dialog, setDialog] = useState<'email' | 'phone' | 'google' | 'unlink' | 'password' | null>(
    null,
  );
  const [verified, setVerified] = useState(false);
  const [reauthCode, setReauthCode] = useState('');
  const [reauthSent, setReauthSent] = useState<Challenge | null>(null);
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [value, setValue] = useState('');
  const [sent, setSent] = useState<Challenge | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    void api
      .get<unknown>('/api/account/me/')
      .then((v) => {
        if (live) {
          const u = customer(v);
          setUser(u);
          setName(u.full_name);
          const linked = new URLSearchParams(window.location.search).get('google');
          if (linked === 'linked') setSuccess('Google is connected to your account.');
          else if (linked === 'already_linked')
            setError(
              'This Google account or sign-in method is already connected. Choose another method or contact support.',
            );
        }
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) window.location.replace('/account/sign-in');
        else if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, []);
  function close() {
    setDialog(null);
    setVerified(false);
    setPassword('');
    setNewPassword('');
    setValue('');
    setSent(null);
    setReauthSent(null);
    setReauthCode('');
    setError('');
  }
  function choose(v: typeof dialog) {
    close();
    setDialog(v);
  }
  async function reauthenticate() {
    setBusy(true);
    setError('');
    try {
      await api.post(
        '/api/account/reauthenticate/',
        user?.has_password
          ? { password }
          : { challenge_id: reauthSent?.challenge_id, code: reauthCode },
      );
      if (dialog !== 'password') setPassword('');
      setVerified(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  if (!user)
    return (
      <AuthShell title="Your account">
        <AuthCard title="Your account">{error ? <Alert>{error}</Alert> : <Loading />}</AuthCard>
      </AuthShell>
    );
  return (
    <div className="min-h-dvh bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex min-h-20 max-w-4xl items-center justify-between px-6">
          <a href="/" className="text-lg font-bold">
            MateCart
          </a>
          <Button
            variant="secondary"
            busy={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.post('/api/account/logout/');
                window.location.assign('/account/sign-in');
              } catch (e) {
                setError(errorMessage(e));
                setBusy(false);
              }
            }}
          >
            Sign out
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-5 py-10">
        <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">My account</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          Hello, {user.full_name || 'welcome'}.
        </h1>
        <p className="mt-3 mb-8 text-sm text-muted">
          Your profile and sign-in methods, all in one place.
        </p>
        {success && (
          <div className="mb-5">
            <Alert tone="success">{success}</Alert>
          </div>
        )}
        {error && !dialog && (
          <div className="mb-5">
            <Alert>{error}</Alert>
          </div>
        )}
        <section className="mb-6 rounded-base border border-line bg-surface p-6">
          <h2 className="mb-5 text-lg font-bold">Your profile</h2>
          <form
            className="grid max-w-md gap-4"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError('');
              try {
                const u = customer(
                  await api.patch<unknown>('/api/account/me/', { full_name: name }),
                );
                setUser(u);
                setSuccess('Your name has been updated.');
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <TextField
              label="Full name"
              required
              maxLength={150}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Button type="submit" busy={busy} className="justify-self-start">
              Save changes
            </Button>
          </form>
        </section>
        <section className="rounded-base border border-line bg-surface p-6">
          <h2 className="text-lg font-bold">Ways to sign in</h2>
          <p className="mt-2 text-sm leading-6 text-muted">
            Add a verified method to this account. You will confirm it is you before making changes.
          </p>
          <div className="mt-3 divide-y divide-line">
            {[
              {
                key: 'email' as const,
                label: 'Email',
                detail: user.email ?? 'No email linked',
                enabled: true,
              },
              {
                key: 'phone' as const,
                label: 'Phone',
                detail: user.phone ?? 'No phone linked',
                enabled: !!available.value?.phone,
              },
              {
                key: 'google' as const,
                label: 'Google',
                detail: user.google_linked ? 'Connected' : 'Not connected',
                enabled: !!available.value?.google,
              },
            ].map((m) => (
              <div key={m.key} className="flex flex-wrap items-center justify-between gap-4 py-5">
                <div>
                  <h3 className="text-sm font-semibold">{m.label}</h3>
                  <p className="mt-1 text-sm text-muted">{m.detail}</p>
                </div>
                <Button
                  variant="secondary"
                  disabled={!m.enabled && !(m.key === 'google' && user.google_linked)}
                  onClick={() =>
                    choose(m.key === 'google' && user.google_linked ? 'unlink' : m.key)
                  }
                >
                  {m.key === 'google'
                    ? user.google_linked
                      ? 'Disconnect'
                      : m.enabled
                        ? 'Connect Google'
                        : 'Unavailable'
                    : m.enabled
                      ? user[m.key]
                        ? 'Change'
                        : 'Add'
                      : 'Unavailable'}
                </Button>
              </div>
            ))}
            <div className="flex flex-wrap items-center justify-between gap-4 py-5">
              <div>
                <h3 className="text-sm font-semibold">Password</h3>
                <p className="mt-1 text-sm text-muted">
                  {user.has_password
                    ? 'A password is set.'
                    : 'Add a verified email before setting a password.'}
                </p>
              </div>
              <Button variant="secondary" disabled={!user.email} onClick={() => choose('password')}>
                {user.has_password ? 'Change password' : 'Set password'}
              </Button>
            </div>
          </div>
        </section>
        <p className="mt-6 text-xs leading-6 text-muted">
          These are your account sign-in details. Delivery names, phone numbers and addresses are
          chosen separately at checkout.
        </p>
      </main>
      {dialog && (
        <Dialog
          title={
            dialog === 'password'
              ? 'Update password'
              : dialog === 'unlink'
                ? 'Disconnect Google?'
                : `Link ${dialog === 'google' ? 'Google' : `your ${dialog}`}`
          }
          onClose={close}
        >
          {error && (
            <div className="mb-4">
              <Alert>{error}</Alert>
            </div>
          )}
          {!verified ? (
            <form
              className="grid gap-5"
              onSubmit={(e) => {
                e.preventDefault();
                void reauthenticate();
              }}
            >
              <p className="text-sm text-muted">First, confirm that this is your account.</p>
              {user.has_password ? (
                <PasswordField
                  label="Current password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              ) : reauthSent ? (
                <>
                  <p className="text-sm text-muted">We sent a code to {reauthSent.destination}.</p>
                  <OTPInput value={reauthCode} onChange={setReauthCode} />
                  <ResendCode
                    seconds={reauthSent.resend_in}
                    onResend={async () =>
                      setReauthSent(
                        challenge(
                          await api.post<unknown>('/api/account/code/resend/', {
                            challenge_id: reauthSent.challenge_id,
                          }),
                        ),
                      )
                    }
                  />
                </>
              ) : (
                <Button
                  variant="secondary"
                  type="button"
                  onClick={async () => {
                    try {
                      setReauthSent(
                        challenge(await api.post<unknown>('/api/account/reauthenticate/code/')),
                      );
                    } catch (e) {
                      setError(errorMessage(e));
                    }
                  }}
                >
                  Send me a confirmation code
                </Button>
              )}
              <Button type="submit" busy={busy} disabled={!user.has_password && !reauthSent}>
                Confirm it is me
              </Button>
            </form>
          ) : dialog === 'google' || dialog === 'unlink' ? (
            <div className="grid gap-5">
              <p className="text-sm text-muted">
                {dialog === 'google'
                  ? 'You will securely continue with Google to prove control of your Google account.'
                  : 'Make sure you can sign in with another verified method before disconnecting Google.'}
              </p>
              <Button
                busy={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    if (dialog === 'google') {
                      const o = object(await api.post<unknown>('/api/account/link/google/start/'));
                      const url = new URL(string(o.url));
                      if (url.origin !== 'https://accounts.google.com')
                        throw new Error('Invalid provider');
                      window.location.assign(url.href);
                    } else {
                      setUser(
                        customer(await api.post<unknown>('/api/account/link/google/remove/')),
                      );
                      close();
                      setSuccess('Google disconnected.');
                    }
                  } catch (e) {
                    setError(errorMessage(e));
                    setBusy(false);
                  }
                }}
              >
                {dialog === 'google' ? 'Continue with Google' : 'Disconnect Google'}
              </Button>
            </div>
          ) : dialog === 'password' ? (
            <form
              className="grid gap-5"
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                try {
                  await api.post('/api/account/password/change/', {
                    current_password: password,
                    new_password: newPassword,
                  });
                  setUser({ ...user, has_password: true });
                  close();
                  setSuccess('Your password has been saved.');
                } catch (err) {
                  setError(errorMessage(err));
                } finally {
                  setBusy(false);
                }
              }}
            >
              <PasswordField
                label="Current password"
                required={user.has_password}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
              <PasswordField
                label="New password"
                required
                minLength={10}
                maxLength={128}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
              />
              <Button type="submit" busy={busy}>
                Save password
              </Button>
            </form>
          ) : sent ? (
            <Verification
              sent={sent}
              onResend={async () =>
                setSent(
                  challenge(
                    await api.post<unknown>('/api/account/code/resend/', {
                      challenge_id: sent.challenge_id,
                    }),
                  ),
                )
              }
              onVerify={async (code) => {
                setUser(
                  customer(
                    await api.post<unknown>(`/api/account/link/${dialog}/verify/`, {
                      challenge_id: sent.challenge_id,
                      code,
                    }),
                  ),
                );
                close();
                setSuccess('Your verified sign-in method has been updated.');
              }}
            />
          ) : (
            <form
              className="grid gap-5"
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                try {
                  setSent(
                    challenge(
                      await api.post<unknown>(`/api/account/link/${dialog}/start/`, {
                        [dialog]: value,
                      }),
                    ),
                  );
                } catch (err) {
                  setError(errorMessage(err));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {dialog === 'email' ? (
                <TextField
                  label="New email address"
                  type="email"
                  required
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              ) : (
                <PhoneField
                  label="Mobile phone number"
                  required
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              )}
              <Button type="submit" busy={busy}>
                Send verification code
              </Button>
            </form>
          )}
        </Dialog>
      )}
    </div>
  );
}

export function CustomerApp({ path }: { path: string }) {
  if (path === 'forgot-password' || path === 'reset-password')
    return (
      <PasswordRecovery
        api={api}
        base="/api/account"
        reset={path === 'reset-password'}
        loginPath="/account/sign-in"
      />
    );
  if (path === 'sign-in' || path === 'register')
    return <CustomerSignIn register={path === 'register'} />;
  if (path === '') return <Account />;
  return (
    <AuthShell title="Page not found">
      <AuthCard title="Let’s get you back">
        <a href="/account" className="text-sm text-brand-deep">
          Go to your account
        </a>
      </AuthCard>
    </AuthShell>
  );
}
