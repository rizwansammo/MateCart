import 'server-only';

import type { StorefrontConfig } from '@matecart/shared-contracts';
import { resolveTheme, type ResolvedTheme } from '@matecart/shared-ui';
import { cookies } from 'next/headers';
import { cache } from 'react';

import { LANGUAGE_COOKIE, messages, resolveLanguage, type Messages } from './i18n';
import { isIndexable, trustedOrigin } from './seo';
import { getStorefrontConfig } from './storefront-api';

export interface StoreContext {
  config: StorefrontConfig;
  theme: ResolvedTheme;
  lang: string;
  t: Messages;
}

/** Store configuration, theme and the customer's language for this request (read once). */
export const getStoreContext = cache(async (): Promise<StoreContext> => {
  const [config, jar] = await Promise.all([getStorefrontConfig(), cookies()]);
  const lang = resolveLanguage(
    jar.get(LANGUAGE_COOKIE)?.value,
    config.store.languages,
    config.store.default_language,
  );
  // Business preset + the theme NetaMate assigned + merchant branding (D-069). Only the API's
  // configuration decides the theme: nothing from the request (cookies, query, host) does.
  const theme = resolveTheme(
    config.preset,
    { primaryColor: config.theme.primary_color, accentColor: config.theme.accent_color },
    config.active_theme,
  );
  if (theme.recovery) {
    // For operators only; customers see the preset's default design.
    console.warn(
      `Storefront theme ${config.active_theme} (${theme.recovery}); using ${theme.theme.id}.`,
    );
  }
  return { config, theme, lang, t: messages(lang) };
});

/** The deployment opted in to indexing (`STOREFRONT_INDEXING=allow`) and has a trusted public
 * HTTPS origin. The merchant's own preference is checked separately (`storeIndexable`). */
export function indexingAllowed(): boolean {
  return process.env.STOREFRONT_INDEXING?.trim() === 'allow' && publicOrigin() !== null;
}

/** The trusted public origin (deployment configuration only, never the Host header). */
export function publicOrigin(): URL | null {
  return trustedOrigin(process.env.STOREFRONT_PUBLIC_URL);
}

/** Search engines may index this store: deployment, origin and merchant all allow it. */
export function storeIndexable(config: StorefrontConfig): boolean {
  return isIndexable({
    deploymentAllows: process.env.STOREFRONT_INDEXING?.trim() === 'allow',
    origin: publicOrigin(),
    merchantAllows: config.seo.allow_indexing,
  });
}
