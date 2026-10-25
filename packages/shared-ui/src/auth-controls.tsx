'use client';

import { useEffect, useId, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react';
import { Wordmark } from './wordmark';

export function Button({
  children,
  busy,
  variant = 'primary',
  className = '',
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  busy?: boolean;
  variant?: 'primary' | 'secondary' | 'danger';
}) {
  return (
    <button
      {...props}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-base border px-4 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-deep disabled:cursor-not-allowed disabled:opacity-50 ${variant === 'primary' ? 'border-ink bg-ink text-surface hover:bg-ink-2' : variant === 'danger' ? 'border-danger-fg bg-danger-fg text-surface' : 'border-line-strong bg-surface text-ink hover:border-ink'} ${className}`}
    >
      {busy && (
        <span
          aria-hidden="true"
          className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"
        />
      )}
      {children}
    </button>
  );
}

export function TextField({
  label,
  hint,
  error,
  className = '',
  id: givenId,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string }) {
  const generated = useId();
  const id = givenId ?? generated;
  return (
    <div className="grid gap-1.5">
      <label className="text-[13px] font-semibold" htmlFor={id}>
        {label}
      </label>
      <input
        {...props}
        id={id}
        aria-invalid={!!error || undefined}
        aria-describedby={error || hint ? `${id}-help` : undefined}
        className={`h-[46px] w-full min-w-0 rounded-base border bg-surface px-3 text-[15px] outline-none focus:border-brand-deep focus:ring-3 focus:ring-brand-tint disabled:bg-canvas ${error ? 'border-danger-fg' : 'border-line-strong'} ${className}`}
      />
      {(error || hint) && (
        <p
          id={`${id}-help`}
          className={`text-xs leading-5 ${error ? 'text-danger-fg' : 'text-muted'}`}
        >
          {error || hint}
        </p>
      )}
    </div>
  );
}

export function PasswordField(props: Omit<Parameters<typeof TextField>[0], 'type'>) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <TextField {...props} type={show ? 'text' : 'password'} className="pr-16" />
      <button
        type="button"
        aria-label={show ? 'Hide password' : 'Show password'}
        onClick={() => setShow(!show)}
        className="absolute top-8 right-3 min-h-9 text-xs font-semibold text-muted underline-offset-4 hover:underline focus-visible:outline-2"
      >
        {show ? 'Hide' : 'Show'}
      </button>
    </div>
  );
}

export function PhoneField(props: Omit<Parameters<typeof TextField>[0], 'type'>) {
  return (
    <TextField
      {...props}
      type="tel"
      autoComplete="tel"
      inputMode="tel"
      hint={props.hint ?? 'Include your country code, for example +880 for Bangladesh.'}
    />
  );
}

export function OTPInput({
  value,
  onChange,
  label = 'Six-digit code',
  disabled = false,
  autoFocus = false,
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  return (
    <TextField
      label={label}
      type="text"
      inputMode="numeric"
      autoComplete="one-time-code"
      value={value}
      maxLength={6}
      required
      pattern="[0-9]{6}"
      disabled={disabled}
      autoFocus={autoFocus}
      className="h-14 text-center font-mono text-2xl tracking-[0.4em]"
      placeholder="000000"
      onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
      onPaste={(e) => {
        e.preventDefault();
        onChange(e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6));
      }}
    />
  );
}

export function Alert({
  children,
  tone = 'error',
}: {
  children: ReactNode;
  tone?: 'error' | 'success' | 'info';
}) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={`rounded-base border p-3 text-sm leading-6 ${tone === 'error' ? 'border-danger-fg/20 bg-danger-bg text-danger-fg' : tone === 'success' ? 'border-success-fg/20 bg-success-bg text-success-fg' : 'border-info-fg/20 bg-info-bg text-info-fg'}`}
    >
      {children}
    </div>
  );
}

export function CopyButton({ text, children = 'Copy' }: { text: string; children?: ReactNode }) {
  const [message, setMessage] = useState('');
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="secondary"
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setMessage('Copied');
          } catch {
            setMessage('Select the text and copy it.');
          }
        }}
      >
        {children}
      </Button>
      <span role="status" className="text-xs text-muted">
        {message}
      </span>
    </span>
  );
}

export function ResendCode({
  seconds,
  onResend,
}: {
  seconds: number;
  onResend: () => Promise<void>;
}) {
  const [left, setLeft] = useState(seconds);
  const [busy, setBusy] = useState(false);
  const [restart, setRestart] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    const end = Date.now() + (restart ? 60 : seconds) * 1000;
    const timer = setInterval(
      () => setLeft(Math.max(0, Math.ceil((end - Date.now()) / 1000))),
      1000,
    );
    return () => clearInterval(timer);
  }, [seconds, restart]);
  return (
    <div className="grid gap-2">
      <button
        type="button"
        disabled={left > 0 || busy}
        className="min-h-10 text-sm font-semibold text-brand-deep disabled:text-muted"
        onClick={async () => {
          setBusy(true);
          setError('');
          try {
            await onResend();
            setLeft(60);
            setRestart((n) => n + 1);
          } catch {
            setError('We could not send another code. Please try again shortly.');
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy
          ? 'Sending…'
          : left > 0
            ? `Resend in 0:${String(left).padStart(2, '0')}`
            : 'Resend code'}
      </button>
      {error && <Alert>{error}</Alert>}
    </div>
  );
}

export function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      aria-labelledby={titleId}
      className="fixed inset-0 m-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto rounded-base border border-line bg-surface p-6 text-ink shadow-overlay backdrop:bg-ink/50"
    >
      <div className="mb-5 flex items-center justify-between gap-4">
        <h2 id={titleId} className="text-xl font-bold tracking-tight">
          {title}
        </h2>
        <Button variant="secondary" type="button" aria-label="Close dialog" onClick={onClose}>
          ×
        </Button>
      </div>
      {children}
    </dialog>
  );
}

export function ProgressSteps({ current, labels }: { current: number; labels: string[] }) {
  return (
    <ol aria-label="Setup progress" className="mb-7 flex gap-2">
      {labels.map((label, i) => (
        <li key={label} aria-current={i === current ? 'step' : undefined} className="flex-1">
          <span
            className={`mb-2 block h-1 rounded-base ${i <= current ? 'bg-brand-deep' : 'bg-line'}`}
          />
          <span className={`text-xs ${i === current ? 'font-bold text-ink' : 'text-muted'}`}>
            {i + 1}. {label}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function AuthCard({
  children,
  title,
  description,
}: {
  children: ReactNode;
  title: string;
  description?: string;
}) {
  return (
    <section className="rounded-base border border-line bg-surface p-6 shadow-card sm:p-7">
      <h2 className="text-xl font-bold tracking-tight">{title}</h2>
      {description && <p className="mt-2 mb-6 text-sm leading-6 text-muted">{description}</p>}
      <div className="mt-5 grid gap-5">{children}</div>
    </section>
  );
}

export function AuthShell({
  children,
  realm = 'customer',
  title,
  description,
}: {
  children: ReactNode;
  realm?: 'customer' | 'staff' | 'console';
  title: string;
  description?: string;
}) {
  return (
    <div className="min-h-dvh bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex h-20 max-w-[1100px] items-center justify-between px-5 sm:px-8">
          <a
            href={realm === 'console' ? '/sign-in' : '/'}
            aria-label={realm === 'console' ? 'NetaMate Console' : 'MateCart home'}
          >
            <Wordmark className="text-2xl" />
            {realm === 'console' && (
              <span className="ml-3 border-l border-line pl-3 text-sm text-muted">
                NetaMate Console
              </span>
            )}
          </a>
          {realm !== 'console' && (
            <a
              href={realm === 'staff' ? '/' : '/manage'}
              className="text-sm font-medium text-muted hover:text-ink"
            >
              {realm === 'staff' ? 'Back to store' : 'Manage your store'}{' '}
              <span aria-hidden="true">↗</span>
            </a>
          )}
        </div>
      </header>
      <main className="mx-auto grid max-w-[1100px] gap-9 px-5 py-10 sm:px-8 sm:py-16 lg:grid-cols-[1fr_440px] lg:items-start lg:gap-16">
        <section className="pt-3 lg:pt-12">
          <p className="mb-4 text-xs font-bold tracking-[0.1em] text-brand-deep uppercase">
            {realm === 'customer'
              ? 'Your account, made simple'
              : realm === 'staff'
                ? 'Your business, in good hands'
                : 'NetaMate · Internal access'}
          </p>
          <h1 className="max-w-md text-3xl leading-[1.1] font-bold tracking-[-0.03em] sm:text-[42px]">
            {title}
          </h1>
          <p className="mt-5 max-w-sm text-[15px] leading-7 text-muted">
            {description ??
              (realm === 'customer'
                ? 'One account for an easier shopping experience. Keep your details and sign-in options together.'
                : realm === 'staff'
                  ? 'A clear, secure place to manage your team and take care of your store.'
                  : 'Secure access for the NetaMate team. Two-step verification helps keep every store protected.')}
          </p>
          <ul className="mt-8 hidden gap-4 text-sm text-ink-2 sm:grid">
            {(realm === 'customer'
              ? ['Sign in your way', 'Your details stay yours', 'Help when you need it']
              : ['Access only for your team', 'Security you can understand', 'You stay in control']
            ).map((item) => (
              <li key={item} className="flex gap-3">
                <span aria-hidden="true" className="font-bold text-brand-deep">
                  ✓
                </span>
                {item}
              </li>
            ))}
          </ul>
        </section>
        {children}
      </main>
      <footer className="mx-auto max-w-[1100px] px-5 py-6 text-xs text-muted sm:px-8">
        {realm === 'console'
          ? 'NetaMate Solutions · Authorized personnel only'
          : 'Powered by MateCart · NetaMate Solutions'}
      </footer>
    </div>
  );
}

export function Loading({ children = 'Loading your account…' }: { children?: ReactNode }) {
  return (
    <p role="status" className="p-8 text-sm text-muted">
      {children}
    </p>
  );
}
