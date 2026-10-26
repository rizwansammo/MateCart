'use client';

import { useEffect, useState } from 'react';

import { LANGUAGE_COOKIE } from '@/lib/i18n';

const TEXT = {
  en: {
    title: 'This page could not be loaded',
    body: 'The store is temporarily unavailable. Please try again in a moment.',
    retry: 'Try again',
    home: 'Homepage',
  },
  bn: {
    title: 'পৃষ্ঠাটি লোড করা যায়নি',
    body: 'স্টোরটি সাময়িকভাবে পাওয়া যাচ্ছে না। একটু পরে আবার চেষ্টা করুন।',
    retry: 'আবার চেষ্টা করুন',
    home: 'হোমপেজ',
  },
};

function cookieLanguage(): 'en' | 'bn' {
  return document.cookie.split(';').some((c) => c.trim() === `${LANGUAGE_COOKIE}=bn`) ? 'bn' : 'en';
}

/**
 * Shown when a page cannot be rendered, typically because commerce-api is unreachable. It uses
 * MateCart's neutral tokens because the store theme itself comes from the API. Error details are
 * never shown to customers.
 */
export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [lang, setLang] = useState<'en' | 'bn'>('en');
  useEffect(() => {
    void Promise.resolve().then(() => setLang(cookieLanguage()));
  }, []);
  const text = TEXT[lang];
  return (
    <main
      lang={lang}
      className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-4 px-6 py-16"
    >
      <h1 className="m-0 text-2xl font-bold tracking-tight">{text.title}</h1>
      <p className="m-0 text-muted">{text.body}</p>
      <div className="flex gap-2.5">
        <button
          type="button"
          onClick={reset}
          className="h-11 cursor-pointer rounded-base bg-ink px-5 text-sm font-semibold text-surface"
        >
          {text.retry}
        </button>
        {/* A full reload, so the store's frame is fetched again once the API is back. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- deliberate full reload */}
        <a
          href="/"
          className="inline-flex h-11 items-center rounded-base border border-line px-5 text-sm font-semibold"
        >
          {text.home}
        </a>
      </div>
    </main>
  );
}
