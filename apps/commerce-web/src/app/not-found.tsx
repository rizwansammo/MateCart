import type { Metadata } from 'next';
import Link from 'next/link';

import { StoreShell } from '@/components/store/store-shell';
import { CONTAINER, FOCUS } from '@/components/store/ui';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree } from '@/lib/storefront-api';

export const metadata: Metadata = { title: 'Not found', robots: { index: false, follow: false } };

/**
 * The 404 page for unknown addresses and for products, categories, collections and pages that
 * are missing or unpublished. It renders the store's own frame, so customers can keep browsing.
 */
export default async function NotFound() {
  const [ctx, categories] = await Promise.all([getStoreContext(), getCategoryTree()]);
  const { t } = ctx;
  return (
    <StoreShell ctx={ctx} categories={categories}>
      <section className={`${CONTAINER} flex flex-col items-start gap-4 py-16 lg:py-24`}>
        <p className="m-0 font-store-spec text-sm text-store-muted">404</p>
        <h1 className="m-0 store-display text-[34px] leading-tight tracking-[-0.02em] sm:text-5xl">
          {t.notFoundTitle}
        </h1>
        <p className="m-0 max-w-[520px] text-store-muted">{t.notFoundBody}</p>
        <div className="flex flex-wrap gap-2.5 pt-2">
          <Link
            href="/"
            className={`inline-flex h-11 items-center rounded-base bg-store-action px-5 text-sm font-semibold text-store-on-action hover:bg-store-action-hover ${FOCUS}`}
          >
            {t.backHome}
          </Link>
          <Link
            href="/shop"
            className={`inline-flex h-11 items-center rounded-base border border-store-line bg-store-surface px-5 text-sm font-semibold ${FOCUS}`}
          >
            {t.browseAll}
          </Link>
        </div>
      </section>
    </StoreShell>
  );
}
