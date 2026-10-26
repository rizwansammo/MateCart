import type { PublicProductSummary } from '@matecart/shared-contracts';
import type { PresetId } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';

import { localize, type Messages } from '@/lib/i18n';
import { priceLabel } from '@/lib/money';

import { FOCUS, StoreImage } from './ui';

/**
 * One product card, five presentations (approved prototype ProductCard): editorial 3:4 for
 * Fashion, dense bordered tiles for Grocery, spec-style cards for Electronics, marketplace
 * tiles for General and cover-first cards for Books. Only real data is shown: the default
 * price (or "From" the lowest), the stock state and, for books, the authors. Shopping
 * buttons arrive with the cart (Phase 07), so a card is a link to its product.
 */

export interface CardProps {
  product: PublicProductSummary;
  preset: PresetId;
  lang: string;
  t: Messages;
  /** The store's other language, shown under the name on Grocery cards (bilingual labels). */
  secondaryLang?: string | null;
  priority?: boolean;
}

function Price({
  product,
  t,
  className,
}: {
  product: PublicProductSummary;
  t: Messages;
  className: string;
}) {
  if (!product.price)
    return <span className="text-[13px] text-store-muted">{t.priceUnavailable}</span>;
  return (
    <span className={`tabular-nums ${className}`}>{priceLabel(product.price, t.from).text}</span>
  );
}

export function ProductCard({ product, preset, lang, t, secondaryLang, priority }: CardProps) {
  const name = localize(product.translations, 'name', product.name, lang);
  const href = `/products/${product.slug}` as Route;
  const image = product.image;
  const alt = image?.alt_text || name;
  const img = (fit: 'cover' | 'contain' = 'cover') => (
    <StoreImage
      src={image?.url}
      alt={alt}
      width={image?.width}
      height={image?.height}
      label={name}
      priority={priority}
      fit={fit}
    />
  );
  const soldOut = !product.in_stock;

  if (preset === 'fashion') {
    return (
      <Link href={href} className={`group flex min-w-0 flex-col gap-3 ${FOCUS}`}>
        <span className="relative block aspect-[3/4] overflow-hidden rounded-base bg-store-surface">
          <span className="block h-full w-full transition-transform duration-500 group-hover:scale-[1.02]">
            {img()}
          </span>
          {soldOut && (
            <span className="absolute inset-x-0 bottom-0 flex h-9 items-center justify-center bg-store-ink/80 text-xs font-semibold tracking-[0.08em] text-store-canvas uppercase">
              {t.soldOut}
            </span>
          )}
        </span>
        <span className="flex flex-col gap-1 px-0.5">
          <span className="text-[15px] leading-snug font-medium">{name}</span>
          <Price product={product} t={t} className="text-[15px] font-semibold" />
        </span>
      </Link>
    );
  }

  if (preset === 'books') {
    const authors = product.authors.join(', ');
    return (
      <Link href={href} className={`group flex min-w-0 flex-col gap-3 ${FOCUS}`}>
        <span className="relative flex justify-center rounded-base bg-store-line/50 px-4 pt-4 transition-colors group-hover:bg-store-line/80">
          <span className="block aspect-[2/3] w-full max-w-[190px] overflow-hidden rounded-[2px_4px_4px_2px] shadow-overlay">
            {img()}
          </span>
          <span className="absolute inset-x-0 bottom-0 h-2.5 bg-store-line" aria-hidden="true" />
        </span>
        <span className="flex flex-col gap-1 px-0.5">
          <span className="store-display text-[17px] leading-tight">{name}</span>
          {authors && <span className="text-[13px] text-store-muted">{authors}</span>}
          <span className="mt-0.5 flex flex-wrap items-baseline gap-2">
            <Price product={product} t={t} className="text-[15px] font-bold" />
            {soldOut && (
              <span className="text-xs font-semibold text-store-muted">{t.outOfStock}</span>
            )}
          </span>
        </span>
      </Link>
    );
  }

  if (preset === 'grocery') {
    const other = secondaryLang ? product.translations[secondaryLang]?.name : undefined;
    return (
      <Link
        href={href}
        className={`flex h-full min-w-0 flex-col gap-2 rounded-base border border-store-line bg-store-surface p-2.5 transition-colors hover:border-store-primary ${FOCUS}`}
      >
        <span className="relative block aspect-square overflow-hidden rounded-base bg-store-canvas">
          {img('contain')}
        </span>
        <span className="flex min-h-[38px] flex-col gap-0.5">
          <span className="text-sm leading-tight font-semibold">{name}</span>
          {other && other !== name && (
            <span lang={secondaryLang ?? undefined} className="text-[12.5px] text-store-muted">
              {other}
            </span>
          )}
        </span>
        <span className="mt-auto flex items-center justify-between gap-2">
          <Price product={product} t={t} className="text-[15px] font-bold" />
          {soldOut && (
            <span className="text-xs font-semibold text-store-muted">{t.outOfStock}</span>
          )}
        </span>
      </Link>
    );
  }

  if (preset === 'electronics') {
    return (
      <Link
        href={href}
        className={`flex h-full min-w-0 flex-col gap-3 rounded-base border border-store-line bg-store-surface p-3.5 transition-[border-color,box-shadow] hover:border-store-muted hover:shadow-overlay ${FOCUS}`}
      >
        <span className="relative block aspect-[4/3] overflow-hidden rounded-base bg-store-canvas">
          {img('contain')}
        </span>
        <span className="flex flex-col gap-1.5">
          {product.brand && (
            <span className="text-[11.5px] font-semibold tracking-[0.06em] text-store-muted uppercase">
              {product.brand.name}
            </span>
          )}
          <span className="text-[15px] leading-snug font-semibold">{name}</span>
        </span>
        <span
          className={`font-store-spec text-xs font-semibold ${soldOut ? 'text-danger-fg' : 'text-success-fg'}`}
        >
          {soldOut ? t.outOfStock : t.inStock}
        </span>
        <span className="mt-auto">
          <Price product={product} t={t} className="text-[19px] font-bold" />
        </span>
      </Link>
    );
  }

  // General / multi-category
  return (
    <Link
      href={href}
      className={`flex h-full min-w-0 flex-col gap-2.5 rounded-base border border-store-line bg-store-surface p-2.5 transition-shadow hover:shadow-overlay ${FOCUS}`}
    >
      <span className="relative block aspect-square overflow-hidden rounded-base bg-store-canvas">
        {img()}
      </span>
      <span className="min-h-[38px] text-sm leading-snug font-medium">{name}</span>
      <span className="mt-auto flex flex-wrap items-baseline justify-between gap-2">
        <Price product={product} t={t} className="text-base font-bold" />
        {soldOut && <span className="text-xs font-semibold text-store-muted">{t.outOfStock}</span>}
      </span>
    </Link>
  );
}

const GRID: Record<PresetId, { full: string; listing: string }> = {
  fashion: {
    full: 'grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-3 lg:grid-cols-4 lg:gap-x-5',
    listing: 'grid grid-cols-2 gap-x-3 gap-y-8 md:grid-cols-3 lg:gap-x-5',
  },
  grocery: {
    full: 'grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6',
    listing: 'grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5',
  },
  electronics: {
    full: 'grid grid-cols-2 gap-3.5 md:grid-cols-3 lg:grid-cols-4',
    listing: 'grid grid-cols-2 gap-3.5 md:grid-cols-3',
  },
  general: {
    full: 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5',
    listing: 'grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4',
  },
  books: {
    full: 'grid grid-cols-2 gap-x-5 gap-y-8 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5',
    listing: 'grid grid-cols-2 gap-x-5 gap-y-8 sm:grid-cols-3 xl:grid-cols-4',
  },
};

/** Products per page: a whole number of rows at the preset's widest grid. */
export const PAGE_SIZE: Record<PresetId, number> = {
  fashion: 24,
  grocery: 30,
  electronics: 24,
  general: 24,
  books: 24,
};

export function ProductGrid({
  products,
  layout = 'full',
  ...card
}: Omit<CardProps, 'product' | 'priority'> & {
  products: PublicProductSummary[];
  layout?: 'full' | 'listing';
}) {
  return (
    <ul className={`m-0 list-none p-0 ${GRID[card.preset][layout]}`}>
      {products.map((product, index) => (
        <li key={product.id} className="min-w-0">
          <ProductCard product={product} priority={index < 4} {...card} />
        </li>
      ))}
    </ul>
  );
}
