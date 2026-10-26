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

// Store name, description and indexing come from store configuration in Phases 05-06.
export const metadata: Metadata = {
  title: 'MateCart',
  description: 'MateCart commerce web foundation build.',
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${geist.variable} ${geistMono.variable} ${hindSiliguri.variable} ${instrumentSerif.variable} ${newsreader.variable}`}
    >
      <body className="bg-canvas font-sans text-ink antialiased">{children}</body>
    </html>
  );
}
