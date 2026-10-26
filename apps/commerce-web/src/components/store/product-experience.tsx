'use client';

import type { PublicImage, PublicProductDetail, PublicVariant } from '@matecart/shared-contracts';
import { errorMessage, type PresentationVariant } from '@matecart/shared-ui';
import Link from 'next/link';
import { useState, type ReactNode } from 'react';

import { addToCart } from '@/lib/checkout-client';
import { formatDate, messages } from '@/lib/i18n';
import { formatMoney, saving } from '@/lib/money';
import {
  initialVariant,
  isPreorder,
  isPurchasable,
  optionState,
  packSize,
  selectOption,
} from '@/lib/variants';

import { FOCUS, StoreImage } from './ui';

/**
 * The interactive part of a product page: gallery, option choice, price, stock and the
 * purchase box, all following the selected variant. The selected variant is
 * kept in the URL (?variant=…) so a shared link opens on the same choice. Server-rendered
 * product texts are passed in as `header` and `highlights`.
 */

const ASPECT: Record<PresentationVariant, string> = {
  editorial: 'aspect-[4/5]',
  quickshop: 'aspect-square',
  spec: 'aspect-[4/3]',
  marketplace: 'aspect-square',
  literary: 'aspect-[2/3]',
};
const COLUMNS: Record<PresentationVariant, string> = {
  editorial: 'lg:grid-cols-[1.15fr_1fr]',
  quickshop: 'lg:grid-cols-2',
  spec: 'lg:grid-cols-2',
  marketplace: 'lg:grid-cols-2',
  literary: 'lg:grid-cols-[minmax(0,0.75fr)_minmax(0,1.25fr)]',
};
const SWATCH = /^#[0-9A-Fa-f]{6}$/;

/** The selected variant's own images first, then the product's shared images. */
export function galleryFor(images: PublicImage[], variant: PublicVariant | null): PublicImage[] {
  const own = variant ? images.filter((image) => image.variant === variant.id) : [];
  const shared = images.filter((image) => image.variant === null);
  const result = [...own, ...shared];
  return result.length > 0 ? result : images;
}

function Gallery({
  images,
  look,
  name,
  lang,
  active,
  onSelect,
}: {
  images: PublicImage[];
  look: PresentationVariant;
  name: string;
  lang: string;
  active: number;
  onSelect: (index: number) => void;
}) {
  const t = messages(lang);
  const index = Math.min(active, Math.max(images.length - 1, 0));
  const current = images[index];
  return (
    <div className="flex flex-col gap-2.5 lg:sticky lg:top-40">
      <div
        className={`relative overflow-hidden rounded-base bg-store-surface ${ASPECT[look]} ${look === 'literary' ? 'mx-auto w-full max-w-[420px] shadow-overlay' : ''}`}
      >
        <StoreImage
          src={current?.url}
          alt={current?.alt_text || name}
          width={current?.width}
          height={current?.height}
          label={name}
          priority
          fit={look === 'quickshop' || look === 'spec' ? 'contain' : 'cover'}
        />
      </div>
      {images.length > 1 && (
        <ul className="m-0 grid list-none grid-cols-5 gap-2.5 p-0">
          {images.map((image, i) => (
            <li key={`${image.url}-${i}`}>
              <button
                type="button"
                onClick={() => onSelect(i)}
                aria-label={t.showImage(i + 1)}
                aria-pressed={i === index}
                className={`block aspect-square w-full cursor-pointer overflow-hidden rounded-base border-2 bg-store-surface ${i === index ? 'border-store-ink' : 'border-transparent'} ${FOCUS}`}
              >
                <StoreImage src={image.url} alt="" label={name} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Options({
  product,
  variant,
  lang,
  onChange,
}: {
  product: PublicProductDetail;
  variant: PublicVariant;
  lang: string;
  onChange: (next: PublicVariant) => void;
}) {
  const t = messages(lang);
  const chip = (state: 'selected' | 'available' | 'unavailable') =>
    `min-h-[42px] min-w-12 cursor-pointer rounded-base border-[1.5px] px-3.5 text-[13.5px] font-semibold ${FOCUS} ${
      state === 'selected'
        ? 'border-store-ink bg-store-ink text-store-canvas'
        : state === 'available'
          ? 'border-store-line bg-store-surface hover:border-store-ink'
          : 'border-dashed border-store-line bg-store-surface text-store-muted line-through'
    }`;

  if (product.options.length === 0) {
    if (product.variants.length < 2) return null;
    // Variants without option attributes (e.g. formats): choose by title.
    return (
      <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
        <legend className="mb-2.5 text-[13.5px] font-semibold">{t.variant}</legend>
        <div className="flex flex-wrap gap-2">
          {product.variants.map((v) => {
            const state =
              v.id === variant.id ? 'selected' : isPurchasable(v) ? 'available' : 'unavailable';
            return (
              <button
                key={v.id}
                type="button"
                aria-pressed={v.id === variant.id}
                onClick={() => onChange(v)}
                className={chip(state)}
              >
                {v.title || v.sku}
                {state === 'unavailable' && (
                  <span className="sr-only"> ({t.unavailableOption})</span>
                )}
              </button>
            );
          })}
        </div>
      </fieldset>
    );
  }

  return (
    <>
      {product.options.map((option) => {
        const chosen = variant.options.find((o) => o.attribute === option.code);
        return (
          <fieldset key={option.code} className="m-0 flex flex-col border-0 p-0">
            <legend className="mb-2.5 text-[13.5px] font-semibold">
              {option.name}
              {chosen && <span className="font-normal text-store-muted"> · {chosen.value}</span>}
            </legend>
            <div className="flex flex-wrap gap-2">
              {option.values.map((value) => {
                const state = optionState(product, variant, option.code, value.code);
                const swatch = SWATCH.test(value.swatch) ? value.swatch : null;
                return (
                  <button
                    key={value.code}
                    type="button"
                    aria-pressed={state === 'selected'}
                    onClick={() =>
                      onChange(selectOption(product, variant, option.code, value.code))
                    }
                    className={`${chip(state)} ${swatch ? 'flex items-center gap-2' : ''}`}
                  >
                    {swatch && (
                      <span
                        aria-hidden="true"
                        className="size-4 rounded-badge ring-1 ring-store-ink/20"
                        // Merchant swatch colour, validated as #RRGGBB above.
                        style={{ backgroundColor: swatch }}
                      />
                    )}
                    {value.value}
                    {state === 'unavailable' && (
                      <span className="sr-only"> ({t.unavailableOption})</span>
                    )}
                  </button>
                );
              })}
            </div>
          </fieldset>
        );
      })}
    </>
  );
}

function PriceAndStock({
  variant,
  lang,
  today,
  look,
}: {
  variant: PublicVariant | null;
  lang: string;
  today: string;
  look: PresentationVariant;
}) {
  const t = messages(lang);
  if (!variant) return <p className="m-0 text-store-muted">{t.notAvailable}</p>;
  const price = variant.price;
  const saved = price ? saving(price) : null;
  const preorder = isPreorder(variant, today);
  const purchasable = isPurchasable(variant);
  const badge = !price
    ? { text: t.notAvailable, tone: 'bg-store-line/60 text-store-muted' }
    : preorder && variant.release_date
      ? {
          text: `${t.preorder} · ${t.releases(formatDate(variant.release_date, lang))}`,
          tone: 'bg-info-bg text-info-fg',
        }
      : purchasable
        ? { text: t.inStock, tone: 'bg-success-bg text-success-fg' }
        : { text: t.outOfStock, tone: 'bg-danger-bg text-danger-fg' };
  const pack = packSize(variant);
  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      <div className="flex flex-wrap items-baseline gap-3">
        {price ? (
          <>
            <span className="text-[30px] font-bold tracking-[-0.02em] tabular-nums">
              {formatMoney(price.amount, price.currency)}
            </span>
            {saved && price.compare_at_amount && (
              <>
                <s className="text-[17px] text-store-muted tabular-nums">
                  {formatMoney(price.compare_at_amount, price.currency)}
                </s>
                <span className="rounded-badge bg-success-bg px-2 py-1 text-[12.5px] font-bold text-success-fg">
                  {t.youSave(formatMoney(saved, price.currency))}
                </span>
              </>
            )}
          </>
        ) : (
          <span className="text-lg font-semibold text-store-muted">{t.priceUnavailable}</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[13.5px]">
        <span
          className={`inline-flex h-7 items-center gap-1.5 rounded-base px-2.5 text-[12.5px] font-bold ${badge.tone}`}
        >
          <span aria-hidden="true" className="size-[7px] rounded-full bg-current" />
          {badge.text}
        </span>
        {pack && (
          <span className="text-store-muted">
            {t.packSize}: <b className="text-store-ink">{pack}</b>
          </span>
        )}
        {variant.kind === 'digital' && (
          <span className="text-store-muted">
            {t.format}:{' '}
            <b className="text-store-ink">
              {t.digitalFormat(variant.digital_format.toUpperCase())}
            </b>
          </span>
        )}
        {variant.isbn && (
          <span className="text-store-muted">
            {t.isbn}: <b className="font-store-spec text-store-ink">{variant.isbn}</b>
          </span>
        )}
        {look === 'spec' && (
          <span className="text-store-muted">
            {t.sku}: <b className="font-store-spec text-store-ink">{variant.sku}</b>
          </span>
        )}
      </div>
    </div>
  );
}

function PurchaseBox({
  lang,
  variant,
  cartEnabled = true,
}: {
  lang: string;
  variant: PublicVariant | null;
  cartEnabled?: boolean;
}) {
  const t = messages(lang);
  const [quantity, setQuantity] = useState('1');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const available = cartEnabled && variant !== null && isPurchasable(variant);

  async function add() {
    if (!variant || !available || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      await addToCart(variant.id, quantity);
      setFeedback({ ok: true, text: t.addedToCart });
    } catch (error) {
      setFeedback({ ok: false, text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <label className="flex w-24 flex-col gap-1 text-xs font-semibold text-store-muted">
          {t.quantity}
          <input
            type="number"
            min="0.001"
            max="999.999"
            step={variant?.inventory_unit === 'each' ? '1' : '0.001'}
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            disabled={!available || busy}
            className={`h-12 rounded-base border border-store-line bg-store-surface px-3 text-base text-store-ink ${FOCUS}`}
          />
        </label>
        <button
          type="button"
          disabled={!available || busy || !quantity}
          onClick={() => void add()}
          className={`mt-[17px] h-12 flex-1 rounded-base bg-store-action text-[14.5px] font-semibold text-store-on-action disabled:cursor-not-allowed disabled:opacity-45 ${FOCUS}`}
        >
          {busy ? t.addingToCart : t.addToCart}
        </button>
      </div>
      {feedback && (
        <p
          role="status"
          className={`m-0 text-[13px] ${feedback.ok ? 'text-success-fg' : 'text-danger-fg'}`}
        >
          {feedback.text}{' '}
          {feedback.ok && (
            <Link href="/cart" className={`font-semibold underline ${FOCUS}`}>
              {t.viewCart}
            </Link>
          )}
        </p>
      )}
    </div>
  );
}

export function ProductExperience({
  product,
  look,
  lang,
  today,
  requestedVariant,
  header,
  highlights,
  cartEnabled = true,
}: {
  product: PublicProductDetail;
  look: PresentationVariant;
  lang: string;
  /** Today in the store's time zone (YYYY-MM-DD, from the API's store_today), to tell
   * pre-orders from stock on hand on the same calendar day as availability. */
  today: string;
  requestedVariant: string | null;
  header: ReactNode;
  highlights: ReactNode;
  cartEnabled?: boolean;
}) {
  const [variant, setVariant] = useState<PublicVariant | null>(() =>
    initialVariant(product, requestedVariant),
  );
  const [imageIndex, setImageIndex] = useState(0);

  function choose(next: PublicVariant) {
    setVariant(next);
    setImageIndex(0);
    const url = new URL(window.location.href);
    url.searchParams.set('variant', next.id);
    window.history.replaceState(window.history.state, '', url);
  }

  return (
    <div className={`grid items-start gap-5 lg:gap-12 ${COLUMNS[look]}`}>
      <Gallery
        images={galleryFor(product.images, variant)}
        look={look}
        name={product.name}
        lang={lang}
        active={imageIndex}
        onSelect={setImageIndex}
      />
      <div className="flex min-w-0 flex-col gap-5">
        {header}
        <PriceAndStock variant={variant} lang={lang} today={today} look={look} />
        {variant && <Options product={product} variant={variant} lang={lang} onChange={choose} />}
        {highlights}
        <PurchaseBox lang={lang} variant={variant} cartEnabled={cartEnabled} />
      </div>
    </div>
  );
}
