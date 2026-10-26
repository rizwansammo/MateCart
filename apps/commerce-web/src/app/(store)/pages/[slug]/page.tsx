import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { Breadcrumbs, CONTAINER, PlainText } from '@/components/store/ui';
import { formatDate, localize } from '@/lib/i18n';
import { getStoreContext } from '@/lib/store-context';
import { getContentPage } from '@/lib/storefront-api';

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const [{ slug }, { lang }] = await Promise.all([params, getStoreContext()]);
  const page = await getContentPage(slug);
  if (!page) return {};
  return {
    title: localize(page.translations, 'title', page.title, lang),
    description: localize(page.translations, 'body', page.body, lang).slice(0, 160),
  };
}

/** A published information or policy page (D-063). Drafts are never returned by the API. */
export default async function ContentPageView({ params }: Props) {
  const [{ slug }, ctx] = await Promise.all([params, getStoreContext()]);
  const page = await getContentPage(slug);
  if (!page) notFound();
  const { t, lang } = ctx;
  const title = localize(page.translations, 'title', page.title, lang);
  const body = localize(page.translations, 'body', page.body, lang);
  return (
    <article className={`${CONTAINER} flex max-w-[860px] flex-col gap-5 pt-6 pb-16`}>
      <Breadcrumbs items={[{ label: t.home, href: '/' }, { label: title }]} label={t.home} />
      <header className="flex flex-col gap-2 border-b border-store-line pb-5">
        {page.page_type === 'policy' && (
          <p className="m-0 text-xs font-semibold tracking-[0.1em] text-store-muted uppercase">
            {t.policy}
          </p>
        )}
        <h1 className="m-0 store-display text-[32px] leading-[1.08] tracking-[-0.02em] sm:text-[44px]">
          {title}
        </h1>
        <p className="m-0 text-[13px] text-store-muted">
          {t.updated(formatDate(page.published_at, lang))}
        </p>
      </header>
      <PlainText text={body} className="text-base leading-[1.75]" />
    </article>
  );
}
