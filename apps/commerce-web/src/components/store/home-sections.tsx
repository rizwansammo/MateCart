import type {
  CategoryNode,
  PublicProductSummary,
  StorefrontSection,
} from '@matecart/shared-contracts';
import type { PresetId } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';

import { localize } from '@/lib/i18n';
import type { StoreContext } from '@/lib/store-context';

import { ProductGrid } from './product-card';
import { CONTAINER, FOCUS, PlainText, SectionHeading, StoreImage } from './ui';

/**
 * The homepage as the merchant configured it (Phase 05 content): enabled sections in their
 * saved order, each drawn in the preset's style. Texts follow the customer's language with the
 * default text as fallback; nothing promotional is added. A section without anything real to
 * show (no categories, an empty collection) is left out rather than rendered empty.
 */

export interface HomeData {
  sections: StorefrontSection[];
  categories: CategoryNode[];
  /** Products for each collection highlight, by section index. */
  collections: (PublicProductSummary[] | null)[];
}

/** Internal links only (validated by the API as well): never another site. */
function internalHref(url: string): Route | null {
  return /^\/(?!\/)[^\s\\]*$/.test(url) ? (url as Route) : null;
}

interface Texts {
  title: string;
  subtitle: string;
  body: string;
  ctaLabel: string;
  ctaHref: Route | null;
}

function texts(section: StorefrontSection, lang: string): Texts {
  const pick = (field: string, base: string) => localize(section.translations, field, base, lang);
  const ctaLabel = pick('cta_label', section.cta_label);
  return {
    title: pick('title', section.title),
    subtitle: pick('subtitle', section.subtitle),
    body: pick('body', section.body),
    ctaLabel,
    ctaHref: ctaLabel ? internalHref(section.cta_url) : null,
  };
}

const BUTTON = `inline-flex h-12 items-center rounded-base px-6 text-sm font-semibold ${FOCUS}`;

function HeroButtons({
  copy,
  shopAll,
  primary,
  secondary,
}: {
  copy: Texts;
  shopAll: string;
  primary: string;
  secondary: string;
}) {
  return (
    <div className="relative flex flex-wrap gap-2.5">
      {copy.ctaHref ? (
        <>
          <Link href={copy.ctaHref} className={`${BUTTON} ${primary}`}>
            {copy.ctaLabel}
          </Link>
          {copy.ctaHref !== '/shop' && (
            <Link href="/shop" className={`${BUTTON} ${secondary}`}>
              {shopAll}
            </Link>
          )}
        </>
      ) : (
        <Link href="/shop" className={`${BUTTON} ${primary}`}>
          {shopAll}
        </Link>
      )}
    </div>
  );
}

function Hero({
  section,
  ctx,
  first,
}: {
  section: StorefrontSection;
  ctx: StoreContext;
  first: boolean;
}) {
  const copy = texts(section, ctx.lang);
  const preset = ctx.theme.preset.id;
  const Heading = first ? 'h1' : 'h2';
  const image = section.image_url ? (
    <StoreImage src={section.image_url} alt="" label={copy.title} priority={first} />
  ) : null;

  if (preset === 'fashion') {
    return (
      <section className="relative h-[520px] overflow-hidden bg-store-line/60 lg:h-[640px]">
        {image && <div className="absolute inset-0">{image}</div>}
        {image && (
          <div className="absolute inset-0 bg-linear-to-r from-store-canvas/95 via-store-canvas/80 to-transparent" />
        )}
        <div className={`${CONTAINER} relative flex h-full flex-col justify-end pb-12`}>
          <div className="flex max-w-[640px] flex-col items-start gap-[18px]">
            <Heading className="m-0 store-display text-[44px] leading-[0.98] tracking-[-0.02em] text-balance lg:text-[76px]">
              {copy.title}
            </Heading>
            {copy.subtitle && (
              <p className="m-0 max-w-[460px] text-base leading-relaxed">{copy.subtitle}</p>
            )}
            {copy.body && (
              <PlainText text={copy.body} className="max-w-[460px] text-[15px] text-store-muted" />
            )}
            <HeroButtons
              copy={copy}
              shopAll={ctx.t.shopAll}
              primary="bg-store-action text-store-on-action hover:bg-store-action-hover"
              secondary="border border-store-ink hover:bg-store-ink hover:text-store-canvas"
            />
          </div>
        </div>
      </section>
    );
  }

  if (preset === 'books') {
    return (
      <section className="relative overflow-hidden bg-store-ink text-store-canvas">
        <div className="absolute inset-0 bg-linear-to-r from-store-ink via-store-ink/90 to-store-primary/60" />
        <div
          className={`${CONTAINER} relative grid items-center gap-12 py-10 md:grid-cols-2 lg:py-16`}
        >
          <div className="flex flex-col items-start gap-[18px]">
            <Heading className="m-0 store-display text-[44px] leading-none tracking-[-0.02em] lg:text-[68px]">
              {copy.title}
            </Heading>
            {copy.subtitle && (
              <p className="m-0 font-store-display text-[21px] text-store-canvas/85 italic">
                {copy.subtitle}
              </p>
            )}
            {copy.body && (
              <PlainText
                text={copy.body}
                className="max-w-[500px] leading-relaxed text-store-canvas/75"
              />
            )}
            <HeroButtons
              copy={copy}
              shopAll={ctx.t.shopAll}
              primary="bg-store-canvas text-store-ink hover:bg-store-canvas/90"
              secondary="border border-store-canvas/40 hover:border-store-canvas"
            />
          </div>
          {image && (
            <div className="mx-auto aspect-[2/3] w-[min(260px,60%)] overflow-hidden rounded-[2px_5px_5px_2px] shadow-overlay">
              {image}
            </div>
          )}
        </div>
      </section>
    );
  }

  if (preset === 'electronics') {
    return (
      <section className={`${CONTAINER} pt-6`}>
        <div
          className={`grid items-center gap-8 overflow-hidden rounded-base bg-store-header p-6 text-store-on-header lg:p-10 ${image ? 'md:grid-cols-2' : ''}`}
        >
          <div className="flex flex-col items-start gap-[18px]">
            <Heading className="m-0 store-display text-[34px] leading-[1.02] tracking-[-0.035em] text-balance lg:text-[52px]">
              {copy.title}
            </Heading>
            {copy.subtitle && (
              <p className="m-0 font-store-spec text-[13px] tracking-[0.04em] text-store-on-header/80">
                {copy.subtitle}
              </p>
            )}
            {copy.body && (
              <PlainText text={copy.body} className="max-w-[520px] text-store-on-header/75" />
            )}
            <HeroButtons
              copy={copy}
              shopAll={ctx.t.shopAll}
              primary="bg-store-primary text-store-on-primary hover:opacity-90"
              secondary="border border-store-on-header/25 hover:border-store-on-header/60"
            />
          </div>
          {image && (
            <div className="aspect-square max-h-[420px] w-full overflow-hidden rounded-base bg-store-on-header/5">
              {image}
            </div>
          )}
        </div>
      </section>
    );
  }

  // Grocery (promo panel) and General (marketplace banner)
  const tint = preset === 'grocery' ? 'bg-store-primary/10' : 'bg-store-accent/10';
  return (
    <section className={`${CONTAINER} pt-5`}>
      <div
        className={`relative flex min-h-[300px] flex-col justify-center gap-3.5 overflow-hidden rounded-base p-6 lg:min-h-[320px] lg:p-10 ${tint}`}
      >
        {image && <div className="absolute inset-y-0 right-0 hidden w-[40%] md:block">{image}</div>}
        <Heading className="relative m-0 store-display text-[32px] leading-[1.02] tracking-[-0.03em] text-balance md:max-w-[56%] lg:text-5xl">
          {copy.title}
        </Heading>
        {copy.subtitle && (
          <p className="relative m-0 text-[17px] md:max-w-[56%]">{copy.subtitle}</p>
        )}
        {copy.body && (
          <PlainText text={copy.body} className="relative text-store-muted md:max-w-[56%]" />
        )}
        <HeroButtons
          copy={copy}
          shopAll={ctx.t.shopAll}
          primary="bg-store-action text-store-on-action hover:bg-store-action-hover"
          secondary="border-[1.5px] border-store-ink hover:bg-store-ink hover:text-store-canvas"
        />
      </div>
    </section>
  );
}

const TILE_LIMIT: Record<PresetId, number> = {
  fashion: 3,
  grocery: 8,
  electronics: 6,
  general: 6,
  books: 10,
};

function CategoryShowcase({
  section,
  categories,
  ctx,
  index,
}: {
  section: StorefrontSection;
  categories: CategoryNode[];
  ctx: StoreContext;
  index: number;
}) {
  const preset = ctx.theme.preset.id;
  const shown = categories.slice(0, TILE_LIMIT[preset]);
  if (shown.length === 0) return null;
  const copy = texts(section, ctx.lang);
  const id = `home-section-${index}`;
  const name = (c: CategoryNode) => localize(c.translations, 'name', c.name, ctx.lang);
  const about = (c: CategoryNode) =>
    localize(c.translations, 'description', c.description, ctx.lang);
  const href = (c: CategoryNode) => `/categories/${c.slug}` as Route;

  const grid = {
    fashion: 'grid gap-4 md:grid-cols-3',
    grocery: 'grid grid-cols-3 gap-2.5 sm:grid-cols-4 lg:grid-cols-8',
    electronics: 'grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6',
    general: 'grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6',
    books: 'grid grid-cols-2 border-t border-l border-store-line sm:grid-cols-3 lg:grid-cols-5',
  }[preset];

  return (
    <section aria-labelledby={id} className={`${CONTAINER} flex flex-col gap-6`}>
      <SectionHeading
        preset={preset}
        id={id}
        title={copy.title}
        subtitle={copy.subtitle}
        href="/shop"
        linkLabel={ctx.t.viewAll}
      />
      <ul className={`m-0 list-none p-0 ${grid}`}>
        {shown.map((category) => (
          <li key={category.slug} className="min-w-0">
            {preset === 'books' ? (
              <Link
                href={href(category)}
                className={`flex h-full flex-col gap-1 border-r border-b border-store-line px-4 py-[18px] hover:bg-store-line/40 ${FOCUS}`}
              >
                <span className="font-store-display text-[19px]">{name(category)}</span>
                {about(category) && (
                  <span className="line-clamp-2 text-xs text-store-muted">{about(category)}</span>
                )}
              </Link>
            ) : preset === 'fashion' ? (
              <Link href={href(category)} className={`group flex flex-col gap-3 ${FOCUS}`}>
                <span className="block aspect-[4/5] overflow-hidden rounded-base bg-store-line/60">
                  <StoreImage src={category.image_url} alt="" label={name(category)} />
                </span>
                <span className="store-display text-[28px] leading-none">{name(category)}</span>
                {about(category) && (
                  <span className="line-clamp-1 text-[12.5px] font-semibold tracking-[0.1em] text-store-muted uppercase">
                    {about(category)}
                  </span>
                )}
              </Link>
            ) : preset === 'general' ? (
              <Link href={href(category)} className={`flex flex-col items-center gap-2.5 ${FOCUS}`}>
                <span className="block aspect-square w-full overflow-hidden rounded-base border border-store-line bg-store-surface">
                  <StoreImage src={category.image_url} alt="" label={name(category)} />
                </span>
                <span className="text-center text-[13.5px] font-semibold">{name(category)}</span>
              </Link>
            ) : (
              <Link
                href={href(category)}
                className={`flex h-full flex-col items-start gap-2 rounded-base border border-store-line bg-store-surface p-3 hover:border-store-primary ${FOCUS}`}
              >
                <span
                  className={`block w-full overflow-hidden rounded-base bg-store-canvas ${preset === 'grocery' ? 'aspect-[1.4/1]' : 'aspect-[16/10]'}`}
                >
                  <StoreImage src={category.image_url} alt="" label={name(category)} />
                </span>
                <span className="text-[13px] leading-tight font-bold">{name(category)}</span>
              </Link>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function CollectionHighlight({
  section,
  products,
  ctx,
  index,
}: {
  section: StorefrontSection;
  products: PublicProductSummary[] | null;
  ctx: StoreContext;
  index: number;
}) {
  if (!section.collection || !products || products.length === 0) return null;
  const copy = texts(section, ctx.lang);
  const id = `home-section-${index}`;
  return (
    <section aria-labelledby={id} className={`${CONTAINER} flex flex-col gap-6`}>
      <SectionHeading
        preset={ctx.theme.preset.id}
        id={id}
        title={copy.title || section.collection.name}
        subtitle={copy.subtitle}
        href={`/collections/${section.collection.slug}` as Route}
        linkLabel={ctx.t.viewAll}
      />
      <ProductGrid
        products={products}
        preset={ctx.theme.preset.id}
        lang={ctx.lang}
        t={ctx.t}
        secondaryLang={secondaryLanguage(ctx)}
      />
    </section>
  );
}

function BrandStory({ section, ctx }: { section: StorefrontSection; ctx: StoreContext }) {
  const copy = texts(section, ctx.lang);
  const preset = ctx.theme.preset.id;
  const image = section.image_url ? (
    <StoreImage src={section.image_url} alt="" label={copy.title} />
  ) : null;
  const link = copy.ctaHref && (
    <Link
      href={copy.ctaHref}
      className={`self-start border-b border-current pb-0.5 text-[13px] font-semibold tracking-[0.08em] uppercase hover:opacity-80 ${FOCUS}`}
    >
      {copy.ctaLabel}
    </Link>
  );
  const text = (
    <div className="flex max-w-[480px] flex-col gap-4">
      <h2
        className={`m-0 store-display leading-[1.04] ${preset === 'fashion' || preset === 'books' ? 'text-[36px] lg:text-[44px]' : 'text-[28px] tracking-[-0.02em]'}`}
      >
        {copy.title}
      </h2>
      {copy.subtitle && <p className="m-0 text-[17px] opacity-90">{copy.subtitle}</p>}
      {copy.body && (
        <PlainText text={copy.body} className="text-[15.5px] leading-relaxed opacity-80" />
      )}
      {link}
    </div>
  );

  if (preset === 'books') {
    return (
      <section className="bg-store-primary text-store-on-primary">
        <div className={`${CONTAINER} grid items-center gap-10 py-10 md:grid-cols-2 lg:py-14`}>
          {text}
          {image && (
            <div className="mx-auto aspect-[3/2] w-full max-w-[520px] overflow-hidden rounded-base">
              {image}
            </div>
          )}
        </div>
      </section>
    );
  }
  const panel =
    preset === 'fashion' ? '' : 'rounded-base border border-store-line bg-store-surface p-6 lg:p-8';
  return (
    <section className={CONTAINER}>
      <div className={`grid items-center gap-10 ${image ? 'md:grid-cols-2' : ''} ${panel}`}>
        {image && (
          <div className="aspect-[5/4] overflow-hidden rounded-base bg-store-line/60">{image}</div>
        )}
        {text}
      </div>
    </section>
  );
}

/** Trust items are the merchant's own words, one per line or separated by "·". */
export function trustItems(section: StorefrontSection, lang: string): string[] {
  const copy = texts(section, lang);
  return [copy.subtitle, copy.body]
    .join('\n')
    .split(/\n|·/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function TrustStrip({
  section,
  ctx,
  index,
}: {
  section: StorefrontSection;
  ctx: StoreContext;
  index: number;
}) {
  const items = trustItems(section, ctx.lang);
  if (items.length === 0) return null;
  const preset = ctx.theme.preset.id;
  const copy = texts(section, ctx.lang);
  const id = `home-section-${index}`;
  // As many columns as items (up to four), so no empty cell shows the grid's background.
  const columns =
    ['', 'md:grid-cols-1', 'md:grid-cols-2', 'md:grid-cols-3'][items.length] ?? 'md:grid-cols-4';
  const list = {
    fashion: 'grid gap-px border-y border-store-line bg-store-line',
    books: 'grid gap-px border-y border-store-line bg-store-line',
    grocery: 'grid grid-cols-2 gap-2.5',
    electronics:
      'grid grid-cols-2 gap-px overflow-hidden rounded-base border border-store-line bg-store-line',
    general: 'grid grid-cols-2 gap-2.5',
  }[preset];
  const item = {
    fashion: 'bg-store-canvas px-2 py-7 store-display text-2xl',
    books: 'bg-store-canvas px-2 py-6 font-store-display text-[19px]',
    grocery: 'rounded-base border border-store-line bg-store-surface p-4 text-[14.5px] font-bold',
    electronics: 'bg-store-surface px-[18px] py-4 text-sm font-bold',
    general: 'rounded-base border border-store-line bg-store-surface p-4 text-[14.5px] font-bold',
  }[preset];
  return (
    <section aria-labelledby={id} className={CONTAINER}>
      <h2 id={id} className="sr-only">
        {copy.title}
      </h2>
      <ul className={`m-0 list-none p-0 ${list} ${columns}`}>
        {items.map((text, index) => (
          <li key={`${text}-${index}`} className={item}>
            {text}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Grocery cards show the other language under the name (bilingual shelf labels). */
export function secondaryLanguage(ctx: StoreContext): string | null {
  if (ctx.theme.preset.id !== 'grocery') return null;
  return ctx.config.store.languages.find((code) => code !== ctx.lang) ?? null;
}

/**
 * Whether a section has real public content to show (Phase 06.1). The homepage route uses the
 * same rule to decide on the fallback, so the page is never left without main content.
 * Heroes and brand stories always render: the API returns enabled sections only, and an
 * enabled section always has a title (05.1).
 */
export function isRenderable(
  section: StorefrontSection,
  index: number,
  data: HomeData,
  lang: string,
): boolean {
  switch (section.section_type) {
    case 'category_showcase':
      return data.categories.length > 0;
    case 'collection_highlight':
      return Boolean(section.collection && data.collections[index]?.length);
    case 'trust_strip':
      return trustItems(section, lang).length > 0;
    default:
      return true;
  }
}

export function hasRenderableSection(data: HomeData, lang: string): boolean {
  return data.sections.some((section, index) => isRenderable(section, index, data, lang));
}

export function HomeSections({ data, ctx }: { data: HomeData; ctx: StoreContext }) {
  const preset = ctx.theme.preset.id;
  const editorial = preset === 'fashion' || preset === 'books';
  const firstHero = data.sections.findIndex((s) => s.section_type === 'hero');
  return (
    <div
      className={`flex flex-col ${editorial ? 'gap-10 pb-16 lg:gap-[72px]' : 'gap-8 pb-14 lg:gap-9'}`}
    >
      {firstHero === -1 && <h1 className="sr-only">{ctx.config.store.name}</h1>}
      {data.sections.map((section, index) => {
        const key = `${section.section_type}-${index}`;
        if (!isRenderable(section, index, data, ctx.lang)) return null;
        switch (section.section_type) {
          case 'hero':
            return <Hero key={key} section={section} ctx={ctx} first={index === firstHero} />;
          case 'category_showcase':
            return (
              <CategoryShowcase
                key={key}
                section={section}
                categories={data.categories}
                ctx={ctx}
                index={index}
              />
            );
          case 'collection_highlight':
            return (
              <CollectionHighlight
                key={key}
                section={section}
                products={data.collections[index] ?? null}
                ctx={ctx}
                index={index}
              />
            );
          case 'brand_story':
            return <BrandStory key={key} section={section} ctx={ctx} />;
          case 'trust_strip':
            return <TrustStrip key={key} section={section} ctx={ctx} index={index} />;
        }
      })}
    </div>
  );
}

/**
 * A homepage without sections, or whose sections have nothing to show, still opens on something
 * real: the store's name and texts, and its newest products.
 */
export function HomeFallback({
  ctx,
  products,
}: {
  ctx: StoreContext;
  products: PublicProductSummary[];
}) {
  const { config, t, lang } = ctx;
  const intro =
    localize(config.translations, 'tagline', config.store.tagline, lang) ||
    localize(config.translations, 'description', config.store.description, lang);
  return (
    <div className="flex flex-col gap-10 pb-16">
      <section className={`${CONTAINER} flex flex-col items-start gap-4 pt-10 lg:pt-14`}>
        <h1 className="m-0 store-display text-[40px] leading-none tracking-[-0.02em] lg:text-6xl">
          {config.store.name}
        </h1>
        {intro && <p className="m-0 max-w-[560px] text-base text-store-muted">{intro}</p>}
        <Link
          href="/shop"
          className={`${BUTTON} bg-store-action text-store-on-action hover:bg-store-action-hover`}
        >
          {t.shopAll}
        </Link>
      </section>
      <section aria-labelledby="new-arrivals" className={`${CONTAINER} flex flex-col gap-6`}>
        <SectionHeading
          preset={ctx.theme.preset.id}
          id="new-arrivals"
          title={t.newArrivals}
          href={products.length > 0 ? '/shop' : undefined}
          linkLabel={t.viewAll}
        />
        {products.length > 0 ? (
          <ProductGrid
            products={products}
            preset={ctx.theme.preset.id}
            lang={lang}
            t={t}
            secondaryLang={secondaryLanguage(ctx)}
          />
        ) : (
          <p className="m-0 rounded-base border border-dashed border-store-line bg-store-surface px-6 py-12 text-center text-store-muted">
            {t.emptyCatalogue}
          </p>
        )}
      </section>
    </div>
  );
}
