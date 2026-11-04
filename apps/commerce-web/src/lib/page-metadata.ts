import 'server-only';

import type { Metadata } from 'next';

import { absolute } from './seo';
import { publicOrigin } from './store-context';

/**
 * Per-page search and sharing metadata (D-085): title, description, a canonical URL and Open
 * Graph / Twitter cards. Absolute URLs (canonical, images) are published only with the trusted
 * public origin; robots directives come from the store layout.
 */
export function pageMetadata(options: {
  title?: string;
  /** Use the title as-is (the homepage), without the "· Store" template. */
  absoluteTitle?: boolean;
  description?: string;
  path: string;
  image?: { url: string; alt: string } | null;
  type?: 'website' | 'article';
}): Metadata {
  const origin = publicOrigin();
  const canonical = absolute(origin, options.path);
  const image = options.image ? absolute(origin, options.image.url) : null;
  const description = options.description || undefined;
  return {
    ...(options.title
      ? { title: options.absoluteTitle ? { absolute: options.title } : options.title }
      : {}),
    description,
    ...(canonical ? { alternates: { canonical } } : {}),
    openGraph: {
      title: options.title,
      description,
      type: options.type ?? 'website',
      ...(canonical ? { url: canonical } : {}),
      ...(image ? { images: [{ url: image, alt: options.image?.alt }] } : {}),
    },
    twitter: {
      card: image ? 'summary_large_image' : 'summary',
      title: options.title,
      description,
      ...(image ? { images: [image] } : {}),
    },
  };
}
