'use client';

import { useRouter } from 'next/navigation';
import { useTransition } from 'react';

import { LANGUAGE_COOKIE, LANGUAGE_COOKIE_MAX_AGE, LANGUAGE_NAMES } from '@/lib/i18n';

/**
 * Switches the storefront language. The choice is a first-party cookie (no personal data) that
 * the server reads, so the refreshed page is rendered in the new language from the start.
 * Shown only when the store supports more than one language.
 */
export function LanguageSwitcher({
  languages,
  current,
  label,
  className = '',
}: {
  languages: readonly string[];
  current: string;
  /** Accessible name, e.g. "Language". */
  label: string;
  className?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  if (languages.length < 2) return null;

  function choose(code: string) {
    const secure = window.location.protocol === 'https:' ? '; secure' : '';
    document.cookie = `${LANGUAGE_COOKIE}=${code}; path=/; max-age=${LANGUAGE_COOKIE_MAX_AGE}; samesite=lax${secure}`;
    startTransition(() => router.refresh());
  }

  if (languages.length === 2) {
    const other = languages.find((code) => code !== current) ?? current;
    return (
      <button
        type="button"
        onClick={() => choose(other)}
        disabled={pending}
        lang={other}
        aria-label={`${label}: ${LANGUAGE_NAMES[other] ?? other}`}
        className={`cursor-pointer disabled:cursor-wait disabled:opacity-60 ${className}`}
      >
        {LANGUAGE_NAMES[other] ?? other.toUpperCase()}
      </button>
    );
  }
  return (
    <select
      aria-label={label}
      value={current}
      disabled={pending}
      onChange={(event) => choose(event.target.value)}
      className={className}
    >
      {languages.map((code) => (
        <option key={code} value={code} lang={code}>
          {LANGUAGE_NAMES[code] ?? code}
        </option>
      ))}
    </select>
  );
}
