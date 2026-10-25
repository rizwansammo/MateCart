'use client';

import { useState } from 'react';
import type { Challenge, TotpSetup } from '@matecart/shared-contracts';
import { challenge, object, recoveryCodes, string, totpSetup } from '@matecart/shared-contracts';
import type { ApiClient } from './api';
import { errorMessage } from './api';
import {
  Alert,
  Button,
  CopyButton,
  OTPInput,
  ProgressSteps,
  ResendCode,
  TextField,
} from './auth-controls';

export function RecoveryCodesPanel({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const text = `MateCart recovery codes\n\nKeep these somewhere safe. Each code works once.\n\n${codes.join('\n')}\n`;
  return (
    <div className="grid gap-5">
      <h3 className="text-lg font-bold">Save your recovery codes</h3>
      <p className="text-sm leading-6 text-muted">
        Use a code if you cannot access your authenticator app or email. Each works once. These
        codes are shown only now, so save them somewhere private.
      </p>
      <ul
        aria-label="Recovery codes"
        className="grid grid-cols-2 gap-3 rounded-base border border-line bg-canvas p-4 font-mono text-sm"
      >
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-3">
        <CopyButton text={codes.join('\n')}>Copy all</CopyButton>
        <Button
          variant="secondary"
          type="button"
          onClick={() => {
            try {
              const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
              const link = document.createElement('a');
              link.href = url;
              link.download = 'matecart-recovery-codes.txt';
              link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            } catch {
              setDownloadError('Please copy the codes instead.');
            }
          }}
        >
          Download codes
        </Button>
      </div>
      {downloadError && <Alert>{downloadError}</Alert>}
      <label className="flex items-start gap-3 text-sm">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 accent-brand-deep"
          checked={saved}
          onChange={(e) => setSaved(e.target.checked)}
        />
        I have saved these codes somewhere safe.
      </label>
      <Button disabled={!saved} onClick={onDone}>
        Finish setup
      </Button>
    </div>
  );
}

export function TotpSetupWizard({
  begin,
  confirm,
  onDone,
}: {
  begin: () => Promise<TotpSetup>;
  confirm: (code: string) => Promise<string[] | null>;
  onDone: () => void;
}) {
  const [step, setStep] = useState(0);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [reveal, setReveal] = useState(false);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function start() {
    setBusy(true);
    setError('');
    try {
      setSetup(await begin());
      setStep(1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function verify() {
    setBusy(true);
    setError('');
    try {
      const result = await confirm(code);
      setSetup(null);
      setReveal(false);
      setCodes(result);
      setStep(3);
    } catch (e) {
      setError(errorMessage(e));
      setCode('');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <ProgressSteps current={step} labels={['Start', 'Scan', 'Confirm', 'Save']} />
      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {step === 0 && (
        <div className="grid gap-5">
          <h3 className="text-lg font-bold">Add an authenticator app</h3>
          <p className="text-sm leading-6 text-muted">
            An authenticator app creates a short sign-in code on your phone, even without a mobile
            signal. Install Google Authenticator, Microsoft Authenticator, or another trusted app
            before you start.
          </p>
          <p className="text-sm leading-6 text-muted">
            You will scan a code, confirm that the app works, then save recovery codes.
          </p>
          <Button busy={busy} onClick={() => void start()}>
            I have an app — continue
          </Button>
        </div>
      )}
      {step === 1 && setup && (
        <div className="grid gap-5">
          <h3 className="text-lg font-bold">Scan this QR code</h3>
          <p className="text-sm leading-6 text-muted">
            Open your authenticator app, choose to add an account, then scan this code.
          </p>
          {/* Local, server-generated SVG data URI; never a remote image or analytics request. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={setup.qr_svg}
            width={220}
            height={220}
            alt="Scan this QR code with your authenticator app"
            className="mx-auto border border-line bg-surface p-3"
          />
          <button
            type="button"
            aria-expanded={reveal}
            onClick={() => setReveal(!reveal)}
            className="min-h-10 text-sm font-semibold text-brand-deep"
          >
            {reveal ? 'Hide secret key' : "Can't scan? View secret key"}
          </button>
          {reveal && (
            <div className="grid gap-3 rounded-base border border-line bg-canvas p-4">
              <p className="text-xs text-muted">
                Choose manual entry in your app. Keep this key private.
              </p>
              <code className="text-sm break-all select-all">{setup.secret}</code>
              <CopyButton text={setup.secret.replace(/\s/g, '')}>Copy key</CopyButton>
            </div>
          )}
          <Button
            onClick={() => {
              setReveal(false);
              setStep(2);
            }}
          >
            I have scanned the code
          </Button>
        </div>
      )}
      {step === 2 && (
        <form
          className="grid gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            void verify();
          }}
        >
          <h3 className="text-lg font-bold">Check that your app works</h3>
          <p className="text-sm leading-6 text-muted">
            Enter the six-digit code beside your account in the app. If it just changed, use the
            newest one.
          </p>
          <OTPInput value={code} onChange={setCode} autoFocus />
          <Button type="submit" busy={busy} disabled={code.length !== 6}>
            Verify and turn on
          </Button>
          <button type="button" onClick={() => setStep(1)} className="text-sm text-brand-deep">
            Back to QR code
          </button>
        </form>
      )}
      {step === 3 &&
        (codes ? (
          <RecoveryCodesPanel
            codes={codes}
            onDone={() => {
              setCodes(null);
              onDone();
            }}
          />
        ) : (
          <div className="grid gap-5">
            <Alert tone="success">
              Your authenticator app is ready. Your existing recovery codes still work.
            </Alert>
            <Button onClick={onDone}>Done</Button>
          </div>
        ))}
    </div>
  );
}

export function EmailSetup({
  api,
  base,
  onDone,
  resendPath,
}: {
  api: ApiClient;
  base: string;
  onDone: () => void;
  resendPath?: string;
}) {
  const [sent, setSent] = useState<Challenge | null>(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[] | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function send(resend = false) {
    const endpoint = resend && resendPath ? resendPath : `${base}/email/start/`;
    setSent(
      challenge(
        await api.post<unknown>(
          endpoint,
          resend && resendPath ? { challenge_id: sent?.challenge_id } : { resend },
        ),
      ),
    );
  }
  return codes ? (
    <RecoveryCodesPanel
      codes={codes}
      onDone={() => {
        setCodes(null);
        onDone();
      }}
    />
  ) : confirmed ? (
    <div className="grid gap-5">
      <Alert tone="success">
        Email codes are turned on. Your existing recovery codes still work.
      </Alert>
      <Button onClick={onDone}>Done</Button>
    </div>
  ) : (
    <div className="grid gap-5">
      {!sent ? (
        <>
          <p className="text-sm leading-6 text-muted">
            We will email a six-digit code when you sign in. Confirm that you can receive one before
            turning this on.
          </p>
          <Button
            busy={busy}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await send();
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Send a verification code
          </Button>
        </>
      ) : (
        <form
          className="grid gap-5"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            try {
              const result = recoveryCodes(
                await api.post<unknown>(`${base}/email/confirm/`, {
                  challenge_id: sent.challenge_id,
                  code,
                }),
              );
              setCodes(result);
              setConfirmed(true);
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <p className="text-sm leading-6 text-muted">
            We sent a code to <strong>{sent.destination}</strong>. It expires in 10 minutes. Check
            your spam folder if needed.
          </p>
          <OTPInput value={code} onChange={setCode} />
          <Button type="submit" busy={busy} disabled={code.length !== 6}>
            Verify and turn on
          </Button>
          <ResendCode seconds={sent.resend_in} onResend={() => send(true)} />
        </form>
      )}
      {error && <Alert>{error}</Alert>}
    </div>
  );
}

export function MfaChallengeForm({
  api,
  base,
  onSuccess,
}: {
  api: ApiClient;
  base: string;
  onSuccess: () => void;
}) {
  const query =
    typeof window === 'undefined'
      ? ''
      : (new URLSearchParams(window.location.search).get('methods') ?? '');
  const configured = query.split(',').filter((v) => ['totp', 'email', 'recovery_code'].includes(v));
  const choices = configured.length ? configured : ['totp', 'email', 'recovery_code'];
  const [method, setMethod] = useState(choices[0] ?? 'totp');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState<Challenge | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const labels: Record<string, string> = {
    totp: 'Authenticator app',
    email: 'Email code',
    recovery_code: 'Recovery code',
  };
  async function send(resend = false) {
    setSent(challenge(await api.post<unknown>(`${base}/mfa/email/`, { resend })));
  }
  return (
    <form
      className="grid gap-5"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
          await api.post(`${base}/mfa/verify/`, { method, code, challenge_id: sent?.challenge_id });
          onSuccess();
        } catch (err) {
          setError(errorMessage(err));
          setCode('');
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className="grid gap-2 text-sm font-semibold">
        Verification method
        <select
          value={method}
          className="h-11 rounded-base border border-line-strong bg-surface px-3"
          onChange={(e) => {
            setMethod(e.target.value);
            setCode('');
            setError('');
          }}
        >
          {choices.map((value) => (
            <option key={value} value={value}>
              {labels[value]}
            </option>
          ))}
        </select>
      </label>
      {method === 'email' && !sent && (
        <Button
          type="button"
          variant="secondary"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            setError('');
            try {
              await send();
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          Email me a code
        </Button>
      )}
      {method === 'email' && sent && (
        <p className="text-sm text-muted">Sent to {sent.destination}. Valid for 10 minutes.</p>
      )}
      {method === 'totp' && (
        <p className="text-sm leading-6 text-muted">
          Open your authenticator app and enter the current code.
        </p>
      )}
      {method === 'recovery_code' ? (
        <TextField
          label="Recovery code"
          value={code}
          required
          autoComplete="off"
          onChange={(e) => setCode(e.target.value)}
          hint="Each code works once. Spaces and hyphens are optional."
        />
      ) : (
        <OTPInput value={code} onChange={setCode} />
      )}
      {error && <Alert>{error}</Alert>}
      <Button type="submit" busy={busy} disabled={!code || (method === 'email' && !sent)}>
        Verify and sign in
      </Button>
      {method === 'email' && sent && (
        <ResendCode seconds={sent.resend_in} onResend={() => send(true)} />
      )}
      <p className="text-xs leading-5 text-muted">
        If you cannot use your usual method, select a recovery code above. If you have none, contact
        your store owner or NetaMate support.
      </p>
    </form>
  );
}

export async function beginTotp(api: ApiClient, base: string): Promise<TotpSetup> {
  return totpSetup(await api.post<unknown>(`${base}/totp/setup/`));
}
export async function confirmTotp(
  api: ApiClient,
  base: string,
  code: string,
): Promise<string[] | null> {
  return recoveryCodes(await api.post<unknown>(`${base}/totp/confirm/`, { code }));
}
export function signInDestination(
  value: unknown,
  root: string,
  verify: string,
  enroll = '/enroll',
): string {
  const o = object(value);
  const status = string(o.status);
  if (status === 'signed_in') return root;
  if (status === 'enrollment_required') return enroll;
  if (status === 'mfa_required') {
    const list = Array.isArray(o.methods)
      ? o.methods.filter((m): m is string => typeof m === 'string').join(',')
      : '';
    return `${verify}?methods=${encodeURIComponent(list)}`;
  }
  throw new Error('Invalid sign-in state');
}
