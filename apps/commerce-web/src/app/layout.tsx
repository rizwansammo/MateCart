import type { Metadata } from 'next';
import { Geist, Geist_Mono, Hind_Siliguri, Instrument_Serif, Newsreader } from 'next/font/google';
import type { ReactNode } from 'react';

import './globals.css';

const geist = Geist({ subsets: ['latin'], variable: '--font-geist', display: 'swap' });
const geistMono = Geist_Mono({
  subsets: ['latin'],
  variable: '--font-geist-mono',
  display: 'swap',
});
const hindSiliguri = Hind_Siliguri({
  subsets: ['bengali', 'latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-hind-siliguri',
  display: 'swap',
});
// Preset display faces (Phase 05): Fashion uses Instrument Serif, Bookstore uses Newsreader.
// next/font downloads them at build time and serves them from this app (no runtime Google calls).
const instrumentSerif = Instrument_Serif({
  subsets: ['latin'],
  weight: '400',
  style: ['normal', 'italic'],
  variable: '--font-instrument-serif',
  display: 'swap',
});
const newsreader = Newsreader({
  subsets: ['latin'],
  variable: '--font-newsreader',
  display: 'swap',
});

// Default for every route outside the storefront (dashboard, account, errors): private and
// never indexed. The storefront layout replaces title, description, canonical and robots with
// the store's own (D-085), so no store page advertises the platform.
export const metadata: Metadata = {
  title: 'MateCart',
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    // One root layout serves the English dashboard and the storefront, and a route-independent
    // layout cannot know which language a storefront visitor chose (a cookie) without fetching
    // store configuration for every dashboard request. The storefront shell therefore declares
    // the customer's language on its own wrapper (`lang` in StoreShell), which assistive
    // technology and search engines apply to the content.
    <html
      lang="en"
      className={`${geist.variable} ${geistMono.variable} ${hindSiliguri.variable} ${instrumentSerif.variable} ${newsreader.variable}`}
    >
      <body className="bg-canvas font-sans text-ink antialiased">{children}</body>
    </html>
  );
}
