import type { CategoryNode } from '@matecart/shared-contracts';
import { Wordmark } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';

import { localize } from '@/lib/i18n';
import type { StoreContext } from '@/lib/store-context';

import { LanguageSwitcher } from './language-switcher';
import { CONTAINER, FOCUS } from './ui';

/**
 * Store footer: identity and brand texts, shop links, published footer pages and the store's
 * configured contact details. Nothing is invented: no social links, addresses, delivery
 * promises or payment badges until the merchant configures them in a later phase.
 */
export function StoreFooter({
  ctx,
  categories,
}: {
  ctx: StoreContext;
  categories: CategoryNode[];
}) {
  const { config, t, lang } = ctx;
  const store = config.store;
  const tagline = localize(config.translations, 'tagline', store.tagline, lang);
  const description = localize(config.translations, 'description', store.description, lang);
  const footerText = localize(config.translations, 'footer_text', store.footer_text, lang);
  const heading = 'm-0 text-xs font-bold tracking-[0.08em] text-store-canvas uppercase';
  const link = `text-[13.5px] text-store-canvas/75 hover:text-store-canvas hover:underline ${FOCUS}`;
  const hasContact = Boolean(store.contact_email || store.contact_phone);

  return (
    <footer className="mt-auto bg-store-ink text-store-canvas/75">
      <div className={`${CONTAINER} flex flex-col gap-8 pt-12 pb-7`}>
        <div className="grid grid-cols-2 gap-8 md:grid-cols-[1.6fr_1fr_1fr_1.3fr]">
          <div className="col-span-2 flex flex-col gap-3 md:col-span-1">
            <p className="m-0 font-store-brand text-xl font-bold text-store-canvas">{store.name}</p>
            {(tagline || description) && (
              <p className="m-0 max-w-[320px] text-[13.5px] leading-relaxed">
                {description || tagline}
              </p>
            )}
            {footerText && (
              <p className="m-0 max-w-[320px] text-[13px] leading-relaxed">{footerText}</p>
            )}
          </div>
          <nav aria-label={t.shop} className="flex flex-col gap-2.5">
            <p className={heading}>{t.shop}</p>
            <Link href="/shop" className={link}>
              {t.allProducts}
            </Link>
            {categories.slice(0, 6).map((category) => (
              <Link
                key={category.slug}
                href={`/categories/${category.slug}` as Route}
                className={link}
              >
                {localize(category.translations, 'name', category.name, lang)}
              </Link>
            ))}
          </nav>
          {config.footer_pages.length > 0 && (
            <nav aria-label={t.information} className="flex flex-col gap-2.5">
              <p className={heading}>{t.information}</p>
              {config.footer_pages.map((page) => (
                <Link key={page.slug} href={`/pages/${page.slug}` as Route} className={link}>
                  {localize(page.translations, 'title', page.title, lang)}
                </Link>
              ))}
            </nav>
          )}
          {(hasContact || store.languages.length > 1) && (
            <div className="flex flex-col gap-2.5">
              {hasContact && <p className={heading}>{t.contact}</p>}
              {store.contact_email && (
                <a href={`mailto:${store.contact_email}`} className={link}>
                  <span className="sr-only">{t.email}: </span>
                  {store.contact_email}
                </a>
              )}
              {store.contact_phone && (
                <a href={`tel:${store.contact_phone}`} className={link}>
                  <span className="sr-only">{t.phone}: </span>
                  {store.contact_phone}
                </a>
              )}
              {store.languages.length > 1 && (
                <div className={`flex items-center gap-3 ${hasContact ? 'pt-3' : ''}`}>
                  <span className="text-[13px]">{t.language}</span>
                  <LanguageSwitcher
                    languages={store.languages}
                    current={lang}
                    label={t.language}
                    className="h-8 rounded-base border border-store-canvas/25 px-2.5 text-[13px] font-semibold text-store-canvas hover:border-store-canvas/60"
                  />
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-store-canvas/15 pt-5 text-[12.5px]">
          <span>{t.copyright(new Date().getFullYear(), store.name)}</span>
          <span className="flex-1" />
          <span className="flex items-center gap-2">
            {t.poweredBy}
            <Wordmark className="text-[15px] text-store-canvas" />
          </span>
        </div>
      </div>
    </footer>
  );
}
