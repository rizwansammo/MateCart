import type { ElectronicsDetails, PublicProductDetail } from '@matecart/shared-contracts';
import type { Route } from 'next';
import Link from 'next/link';

import { formatDate, localize, type Messages } from '@/lib/i18n';
import type { StoreContext } from '@/lib/store-context';

import { FOCUS } from './ui';

/**
 * Server-rendered product texts: the title block, short type-specific highlights beside the
 * price, and the full details table. Only fields the merchant filled in are shown, labelled in
 * the customer's language; values are plain text.
 */

export interface FactRow {
  label: string;
  value: string;
  mono?: boolean;
}

function regionName(code: string, lang: string): string {
  try {
    return (
      new Intl.DisplayNames([lang === 'bn' ? 'bn' : 'en'], { type: 'region' }).of(code) ?? code
    );
  } catch {
    return code;
  }
}

type MaybeRow = FactRow | null | false | undefined | '';

function keep(rows: MaybeRow[]): FactRow[] {
  return rows.filter((row): row is FactRow => Boolean(row && row.value.trim()));
}

/** Every filled-in detail for the product's type, then its specifications. */
export function factRows(product: PublicProductDetail, t: Messages, lang: string): FactRow[] {
  const rows: MaybeRow[] = [];
  const book = product.book;
  if (book) {
    rows.push(
      { label: t.authors, value: book.authors.join(', ') },
      { label: t.publisher, value: book.publisher },
      { label: t.edition, value: book.edition },
      book.publication_date && {
        label: t.published,
        value: formatDate(book.publication_date, lang),
      },
      { label: t.bookLanguage, value: t.languageNames[book.language] ?? book.language },
      { label: t.genre, value: book.genre },
      book.page_count !== null && { label: t.pages, value: String(book.page_count) },
      book.series_name && {
        label: t.series,
        value: book.series_number ? `${book.series_name} #${book.series_number}` : book.series_name,
      },
    );
  }
  const apparel = product.apparel;
  if (apparel) {
    rows.push(
      { label: t.audience, value: t.audiences[apparel.audience] ?? apparel.audience },
      { label: t.material, value: apparel.material },
      { label: t.fit, value: apparel.fit },
      { label: t.care, value: apparel.care_instructions },
      { label: t.sizeGuide, value: apparel.size_guide_notes },
    );
  }
  const grocery = product.grocery;
  if (grocery) {
    rows.push(
      { label: t.perishable, value: grocery.is_perishable ? t.yes : t.no },
      { label: t.storage, value: grocery.storage_instructions },
      grocery.shelf_life_days !== null && {
        label: t.shelfLife,
        value: t.days(grocery.shelf_life_days),
      },
      grocery.country_of_origin && {
        label: t.origin,
        value: regionName(grocery.country_of_origin, lang),
      },
      { label: t.ingredients, value: grocery.ingredients },
      { label: t.allergens, value: grocery.allergens },
    );
  }
  const electronics = product.electronics;
  if (electronics) {
    rows.push({ label: t.model, value: electronics.model_number, mono: true });
    if (electronics.warranty)
      rows.push({ label: t.warranty, value: warrantyText(electronics.warranty, t) });
  }
  for (const spec of product.specifications) {
    const value = typeof spec.value === 'boolean' ? (spec.value ? t.yes : t.no) : spec.display;
    rows.push({ label: spec.name, value, mono: product.product_type === 'electronics' });
  }
  return keep(rows);
}

function warrantyText(warranty: NonNullable<ElectronicsDetails['warranty']>, t: Messages): string {
  const provider = t.warrantyProvider[warranty.provider] ?? warranty.provider;
  return `${t.months(warranty.duration_months)} · ${provider}`;
}

export function FactTable({ rows }: { rows: FactRow[] }) {
  return (
    <dl className="m-0 max-w-[760px] overflow-hidden rounded-base border border-store-line bg-store-surface">
      {rows.map((row, index) => (
        <div
          key={`${row.label}-${index}`}
          className="grid grid-cols-[minmax(110px,200px)_1fr] border-b border-store-line text-sm last:border-b-0"
        >
          <dt className="bg-store-canvas px-4 py-3 text-store-muted">{row.label}</dt>
          <dd
            className={`m-0 px-4 py-3 whitespace-pre-line ${row.mono ? 'font-store-spec text-[13px]' : ''}`}
          >
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function ProductHeader({
  product,
  ctx,
}: {
  product: PublicProductDetail;
  ctx: StoreContext;
}) {
  const { t, lang, theme, config } = ctx;
  const preset = theme.preset.id;
  const name = localize(product.translations, 'name', product.name, lang);
  const summary = localize(product.translations, 'summary', product.summary, lang);
  const primary = product.categories.find((c) => c.is_primary) ?? product.categories[0];
  const other = config.store.languages.find((code) => code !== lang);
  const otherName = other ? product.translations[other]?.name : undefined;
  const book = product.book;
  const authors = book?.authors.length ? book.authors : product.authors;
  const size = {
    fashion: 'text-[30px] lg:text-[44px]',
    books: 'text-[30px] lg:text-[40px]',
    grocery: 'text-[26px] lg:text-[32px]',
    electronics: 'text-[26px] lg:text-[32px]',
    general: 'text-[26px] lg:text-[32px]',
  }[preset];

  return (
    <div className="flex flex-col gap-2.5">
      {primary && (
        <Link
          href={`/categories/${primary.slug}` as Route}
          className={`self-start text-xs font-semibold tracking-[0.1em] text-store-muted uppercase hover:text-store-ink ${FOCUS}`}
        >
          {primary.name}
        </Link>
      )}
      <h1 className={`m-0 store-display leading-[1.08] tracking-[-0.02em] ${size}`}>{name}</h1>
      {(preset === 'grocery' || preset === 'general') && otherName && otherName !== name && (
        <p lang={other} className="m-0 text-base text-store-muted">
          {otherName}
        </p>
      )}
      {book?.subtitle && (
        <p className="m-0 font-store-display text-lg text-store-muted italic">{book.subtitle}</p>
      )}
      {authors.length > 0 && (
        <p className="m-0 flex flex-wrap items-baseline gap-2 text-[15px]">
          <span className="text-store-muted">{t.by}</span>
          <span className="font-store-display text-[19px] text-store-primary italic">
            {authors.join(', ')}
          </span>
          {book?.publisher && (
            <>
              <span aria-hidden="true" className="text-store-muted">
                ·
              </span>
              <span className="text-sm">{book.publisher}</span>
            </>
          )}
        </p>
      )}
      {book?.series_name && (
        <span className="self-start rounded-badge bg-store-line/60 px-2.5 py-1 text-[12.5px] font-semibold">
          {book.series_number ? `${book.series_name} #${book.series_number}` : book.series_name}
        </span>
      )}
      {product.brand && (
        <Link
          href={`/shop?brand=${product.brand.slug}` as Route}
          className={`self-start text-sm text-store-muted hover:text-store-ink hover:underline ${FOCUS}`}
        >
          {t.brand}: <span className="font-semibold text-store-ink">{product.brand.name}</span>
        </Link>
      )}
      {summary && (
        <p className="m-0 max-w-[560px] text-[15px] leading-relaxed text-store-muted">{summary}</p>
      )}
    </div>
  );
}

/** A few facts beside the price, in the preset's manner (specs for Electronics, facts for Books). */
export function ProductHighlights({
  product,
  ctx,
}: {
  product: PublicProductDetail;
  ctx: StoreContext;
}) {
  const { t, lang } = ctx;
  const electronics = product.electronics;
  if (electronics || product.product_type === 'electronics') {
    const specs = product.specifications.slice(0, 6);
    return (
      <div className="flex flex-col gap-3">
        {(electronics?.model_number || electronics?.warranty) && (
          <p className="m-0 flex flex-wrap gap-x-4 gap-y-1 text-[13.5px] text-store-muted">
            {electronics.model_number && (
              <span>
                {t.model}:{' '}
                <b className="font-store-spec text-store-ink">{electronics.model_number}</b>
              </span>
            )}
            {electronics.warranty && (
              <span>
                {t.warranty}:{' '}
                <b className="text-store-ink">{warrantyText(electronics.warranty, t)}</b>
              </span>
            )}
          </p>
        )}
        {specs.length > 0 && (
          <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2 p-0">
            {specs.map((spec) => (
              <li
                key={spec.attribute}
                className="flex flex-col gap-0.5 rounded-base border border-store-line bg-store-surface px-3 py-2.5"
              >
                <span className="text-[11.5px] text-store-muted">{spec.name}</span>
                <span className="font-store-spec text-[12.5px] font-medium">
                  {typeof spec.value === 'boolean' ? (spec.value ? t.yes : t.no) : spec.display}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (product.book) {
    const rows = factRows({ ...product, specifications: [] }, t, lang).filter(
      (row) => row.label !== t.authors,
    );
    if (rows.length === 0) return null;
    return (
      <dl className="m-0 grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] border-t border-l border-store-line">
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex flex-col gap-0.5 border-r border-b border-store-line px-3 py-2.5"
          >
            <dt className="text-[11px] tracking-[0.08em] text-store-muted uppercase">
              {row.label}
            </dt>
            <dd className="m-0 text-[13.5px]">{row.value}</dd>
          </div>
        ))}
      </dl>
    );
  }
  const apparel = product.apparel;
  if (apparel && (apparel.material || apparel.fit || apparel.size_guide_url)) {
    return (
      <p className="m-0 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13.5px] text-store-muted">
        {apparel.material && (
          <span>
            {t.material}: <b className="text-store-ink">{apparel.material}</b>
          </span>
        )}
        {apparel.fit && (
          <span>
            {t.fit}: <b className="text-store-ink">{apparel.fit}</b>
          </span>
        )}
        {apparel.size_guide_url && (
          <a
            href={apparel.size_guide_url}
            target="_blank"
            rel="noopener"
            className={`font-semibold text-store-ink underline ${FOCUS}`}
          >
            {t.sizeGuide}
          </a>
        )}
      </p>
    );
  }
  return null;
}

/** The heading of the details tab, after the product's kind. */
export function detailsLabel(product: PublicProductDetail, t: Messages): string {
  if (product.book) return t.bookDetails;
  if (product.electronics || product.product_type === 'electronics') return t.specifications;
  if (product.apparel) return t.sizeAndFit;
  if (product.grocery) return t.storageAndOrigin;
  return t.details;
}
