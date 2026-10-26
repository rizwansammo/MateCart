import type { CategoryNode } from '@matecart/shared-contracts';
import type { PresentationVariant } from '@matecart/shared-ui';
import type { Route } from 'next';
import Form from 'next/form';
import Link from 'next/link';

import { localize } from '@/lib/i18n';
import type { StoreContext } from '@/lib/store-context';

import { LanguageSwitcher } from './language-switcher';
import { CartLink } from './cart-link';
import { MenuDrawer } from './mobile-menu';
import { NavLinks, type NavItem } from './nav-links';
import { CONTAINER, ChevronRight, FOCUS, MenuIcon, SearchIcon, UserIcon } from './ui';

/**
 * The storefront header (approved prototype Storefront headers): one component, five layouts,
 * chosen by the theme's `presentation.header` variant (D-069). Editorial and literary centre the
 * brand between navigation and search; quickshop and marketplace are search-first with a
 * category row; spec is a dark technical header. Every variant offers the same functions:
 * search, category navigation, account, language and the mobile drawer (functional parity).
 * Below the `lg` breakpoint every variant uses one compact row with a menu drawer.
 * Cart, wishlist, compare and notifications are not shown until their phases exist.
 */

const NAV_LIMIT = 8;

function Brand({ ctx, className = '' }: { ctx: StoreContext; className?: string }) {
  const { config, theme } = ctx;
  const header = theme.presentation.header;
  const logo =
    theme.theme.layout.darkHeader && config.theme.logo_dark_url
      ? config.theme.logo_dark_url
      : config.theme.logo_url;
  const showName = !logo || config.theme.show_name_with_logo;
  const name = config.store.name;
  return (
    <Link href="/" className={`flex min-w-0 items-center gap-2.5 ${className} ${FOCUS}`}>
      {logo ? (
        // eslint-disable-next-line @next/next/no-img-element -- API-served logo; no image optimiser
        <img
          src={logo}
          alt={showName ? '' : name}
          className="h-8 w-auto max-w-[160px] object-contain"
        />
      ) : (
        header === 'quickshop' && (
          <span
            aria-hidden="true"
            className="size-[26px] flex-none rounded-badge bg-store-primary"
          />
        )
      )}
      {showName && (
        <span
          className={
            {
              editorial: 'truncate font-store-brand text-[26px] leading-none lg:text-[34px]',
              literary:
                'truncate font-store-brand text-[26px] leading-none font-medium lg:text-[34px]',
              quickshop: 'truncate text-xl font-extrabold tracking-[-0.02em] lg:text-[22px]',
              spec: 'truncate font-store-brand text-base font-semibold tracking-[0.14em] uppercase lg:text-lg',
              marketplace: 'truncate text-xl font-extrabold tracking-[-0.03em] lg:text-2xl',
            }[header]
          }
        >
          {name}
          {header === 'spec' && (
            <span aria-hidden="true" className="text-store-primary">
              _
            </span>
          )}
        </span>
      )}
    </Link>
  );
}

const SEARCH: Record<
  PresentationVariant,
  { form: string; input: string; button: string; text: boolean }
> = {
  editorial: {
    form: 'flex h-9 w-[200px] items-center gap-2 border-b border-store-line',
    input: 'min-w-0 flex-1 bg-transparent text-[13.5px] outline-none',
    button: 'text-store-muted',
    text: false,
  },
  literary: {
    form: 'flex h-[42px] w-full max-w-[340px] items-center gap-2.5 rounded-base border border-store-line bg-store-surface px-3',
    input: 'min-w-0 flex-1 bg-transparent text-sm outline-none',
    button: 'text-store-muted',
    text: false,
  },
  quickshop: {
    form: 'flex h-[46px] w-full items-center gap-2.5 rounded-base border border-store-line bg-store-canvas px-3.5',
    input: 'min-w-0 flex-1 bg-transparent text-[14.5px] outline-none',
    button: 'text-store-muted',
    text: false,
  },
  spec: {
    form: 'flex h-11 w-full items-center overflow-hidden rounded-base border border-store-header-line bg-store-on-header/5',
    input:
      'min-w-0 flex-1 bg-transparent px-3.5 text-sm text-store-on-header outline-none placeholder:text-store-on-header/50',
    button:
      'flex h-full w-[52px] items-center justify-center bg-store-primary text-store-on-primary',
    text: false,
  },
  marketplace: {
    form: 'flex h-11 w-full items-center overflow-hidden rounded-base border-[1.5px] border-store-ink bg-store-surface',
    input: 'min-w-0 flex-1 bg-transparent px-3.5 text-sm outline-none',
    button:
      'h-full bg-store-action px-[18px] text-[13.5px] font-bold text-store-on-action hover:bg-store-action-hover',
    text: true,
  },
};

function SearchForm({ ctx, id }: { ctx: StoreContext; id: string }) {
  const header = ctx.theme.presentation.header;
  const style = SEARCH[header];
  // The wording follows the catalogue (books are found by title, author or ISBN), not the theme.
  const placeholder =
    ctx.theme.preset.id === 'books' ? ctx.t.searchBooksPlaceholder : ctx.t.searchPlaceholder;
  const button = (
    <button
      type="submit"
      aria-label={ctx.t.search}
      className={`flex-none cursor-pointer ${style.button} ${FOCUS}`}
    >
      {style.text ? ctx.t.search : <SearchIcon width={18} height={18} />}
    </button>
  );
  const iconFirst = header === 'editorial' || header === 'literary' || header === 'quickshop';
  return (
    <Form action="/search" role="search" className={style.form}>
      {iconFirst && button}
      <label htmlFor={id} className="sr-only">
        {ctx.t.searchPlaceholder}
      </label>
      <input
        id={id}
        name="q"
        type="search"
        placeholder={placeholder}
        maxLength={100}
        autoComplete="off"
        className={style.input}
      />
      {!iconFirst && button}
    </Form>
  );
}

function AccountLink({
  ctx,
  className,
  showLabel,
}: {
  ctx: StoreContext;
  className: string;
  showLabel: boolean;
}) {
  return (
    <Link
      href="/account"
      aria-label={showLabel ? undefined : ctx.t.account}
      className={`flex items-center gap-2 ${className} ${FOCUS}`}
    >
      <UserIcon />
      {showLabel && <span>{ctx.t.account}</span>}
    </Link>
  );
}

function Language({ ctx, className }: { ctx: StoreContext; className: string }) {
  return (
    <LanguageSwitcher
      languages={ctx.config.store.languages}
      current={ctx.lang}
      label={ctx.t.language}
      className={className}
    />
  );
}

function MobileDrawer({ ctx, categories }: { ctx: StoreContext; categories: CategoryNode[] }) {
  const { t, lang } = ctx;
  const row = `flex min-h-11 items-center justify-between rounded-base px-2 text-[15px] hover:bg-store-canvas ${FOCUS}`;
  return (
    <MenuDrawer
      trigger={<MenuIcon width={22} height={22} />}
      triggerLabel={t.menu}
      title={ctx.config.store.name}
      closeLabel={t.close}
      triggerClassName="flex size-11 items-center justify-center rounded-base"
    >
      <nav aria-label={t.menu} className="flex flex-col gap-1">
        <Link
          href="/account"
          className={`mb-2 justify-start! gap-3 bg-store-canvas font-semibold ${row}`}
        >
          <UserIcon /> {t.account}
        </Link>
        <p className="px-2 pt-2 pb-1 text-[11.5px] font-bold tracking-[0.08em] text-store-muted uppercase">
          {t.categories}
        </p>
        <Link href="/shop" className={row}>
          {t.allProducts}
          <ChevronRight width={16} height={16} className="text-store-muted" />
        </Link>
        {categories.map((category) => (
          <div key={category.slug} className="flex flex-col">
            <Link href={`/categories/${category.slug}` as Route} className={row}>
              {localize(category.translations, 'name', category.name, lang)}
              <ChevronRight width={16} height={16} className="text-store-muted" />
            </Link>
            {category.children.map((child) => (
              <Link
                key={child.slug}
                href={`/categories/${child.slug}` as Route}
                className={`${row} pl-6 text-sm text-store-muted`}
              >
                {localize(child.translations, 'name', child.name, lang)}
              </Link>
            ))}
          </div>
        ))}
        {ctx.config.footer_pages.length > 0 && (
          <>
            <p className="px-2 pt-4 pb-1 text-[11.5px] font-bold tracking-[0.08em] text-store-muted uppercase">
              {t.information}
            </p>
            {ctx.config.footer_pages.map((page) => (
              <Link
                key={page.slug}
                href={`/pages/${page.slug}` as Route}
                className={`${row} text-[14.5px]`}
              >
                {localize(page.translations, 'title', page.title, lang)}
              </Link>
            ))}
          </>
        )}
      </nav>
      {ctx.config.store.languages.length > 1 && (
        <div className="mt-4 flex items-center justify-between border-t border-store-line px-2 pt-4">
          <span className="text-[13px] text-store-muted">{t.language}</span>
          <Language
            ctx={ctx}
            className="h-9 rounded-base border border-store-line bg-store-surface px-3 font-semibold"
          />
        </div>
      )}
    </MenuDrawer>
  );
}

const NAV: Record<PresentationVariant, { list: string; link: string; active: string }> = {
  editorial: {
    list: 'flex flex-wrap gap-x-[22px] gap-y-1',
    link: 'border-b-[1.5px] border-transparent py-1.5 text-[12.5px] font-semibold tracking-[0.1em] uppercase hover:border-store-ink',
    active: 'border-store-ink!',
  },
  literary: {
    list: 'flex justify-center gap-[26px] overflow-x-auto overflow-y-hidden',
    link: 'block border-b-2 border-transparent py-3 font-store-display text-base hover:border-store-line',
    active: 'border-store-primary!',
  },
  quickshop: {
    list: 'flex gap-2 overflow-x-auto overflow-y-hidden pb-3',
    link: 'flex h-[34px] items-center rounded-base border border-store-line bg-store-surface px-3.5 text-[13px] font-semibold whitespace-nowrap hover:border-store-primary',
    active: 'border-store-primary! bg-store-primary/10',
  },
  spec: {
    list: 'flex h-11 items-center gap-6 overflow-x-auto overflow-y-hidden',
    link: 'text-[13px] font-semibold whitespace-nowrap text-store-on-header/70 hover:text-store-on-header',
    active: 'text-store-on-header!',
  },
  marketplace: {
    list: 'flex h-11 items-center gap-6 overflow-x-auto overflow-y-hidden',
    link: 'text-[13.5px] font-medium whitespace-nowrap hover:underline',
    active: 'font-bold',
  },
};

export function StoreHeader({
  ctx,
  categories,
}: {
  ctx: StoreContext;
  categories: CategoryNode[];
}) {
  const { config, theme, t, lang } = ctx;
  const header = theme.presentation.header;
  const announcement = localize(
    config.translations,
    'announcement',
    config.store.announcement,
    lang,
  );
  const navItems: NavItem[] = [
    { label: t.shopAll, href: '/shop' },
    ...categories.slice(0, NAV_LIMIT).map((category) => ({
      label: localize(category.translations, 'name', category.name, lang),
      href: `/categories/${category.slug}` as Route,
    })),
  ];
  const nav = (className = '') => (
    <div className={className}>
      <NavLinks
        items={navItems}
        label={t.categories}
        className={NAV[header].list}
        linkClassName={NAV[header].link}
        activeClassName={NAV[header].active}
      />
    </div>
  );
  const dark = theme.theme.layout.darkHeader;
  const iconButton = `flex size-11 items-center justify-center rounded-base ${FOCUS}`;
  const langButton =
    'h-9 rounded-base px-2 text-[12.5px] font-semibold opacity-80 hover:opacity-100';

  return (
    <header className="sticky top-0 z-20 border-b border-store-header-line bg-store-header text-store-on-header">
      {announcement && (
        <p
          className={`m-0 flex min-h-[34px] items-center justify-center px-4 py-1.5 text-center text-xs ${
            {
              editorial: 'bg-store-ink tracking-[0.04em] text-store-canvas',
              literary: 'bg-store-primary tracking-[0.02em] text-store-on-primary',
              quickshop: 'bg-store-primary font-semibold text-store-on-primary',
              spec: 'border-b border-store-header-line font-store-spec text-store-on-header/80',
              marketplace: 'border-b border-store-line bg-store-canvas text-store-muted',
            }[header]
          }`}
        >
          {announcement}
        </p>
      )}

      {/* Compact header below lg */}
      <div className="lg:hidden">
        <div className="flex h-14 items-center gap-1 px-2">
          <MobileDrawer ctx={ctx} categories={categories} />
          <Brand ctx={ctx} className="flex-1 px-1" />
          {(header === 'editorial' || header === 'literary') && (
            <Link href="/search" aria-label={t.search} className={iconButton}>
              <SearchIcon />
            </Link>
          )}
          <AccountLink ctx={ctx} className={iconButton} showLabel={false} />
          {ctx.config.modules.includes('cart_checkout') && <CartLink label={t.cart} compact />}
        </div>
        {(header === 'quickshop' || header === 'marketplace' || header === 'spec') && (
          <div className="px-3 pb-3">
            <SearchForm ctx={ctx} id="store-search-mobile" />
          </div>
        )}
      </div>

      {/* Desktop headers */}
      <div className="hidden lg:block">
        {(header === 'editorial' || header === 'literary') && (
          <>
            <div
              className={`${CONTAINER} grid h-[84px] grid-cols-[1fr_auto_1fr] items-center gap-6`}
            >
              {header === 'editorial' ? nav() : <SearchForm ctx={ctx} id="store-search" />}
              <div className="flex flex-col items-center gap-1">
                <Brand ctx={ctx} />
                {header === 'literary' && config.store.tagline && (
                  <span className="text-[10.5px] tracking-[0.32em] text-store-muted uppercase">
                    {localize(config.translations, 'tagline', config.store.tagline, lang)}
                  </span>
                )}
              </div>
              <div className="flex items-center justify-end gap-1.5">
                {header === 'editorial' && <SearchForm ctx={ctx} id="store-search" />}
                <Language ctx={ctx} className={langButton} />
                <AccountLink
                  ctx={ctx}
                  className={`${iconButton} ${header === 'literary' ? 'w-auto! px-2.5 text-[13.5px]' : ''}`}
                  showLabel={header === 'literary'}
                />
                {ctx.config.modules.includes('cart_checkout') && (
                  <CartLink label={t.cart} compact={header === 'editorial'} />
                )}
              </div>
            </div>
            {header === 'literary' && nav(`border-t border-store-line ${CONTAINER}`)}
          </>
        )}

        {(header === 'quickshop' || header === 'marketplace' || header === 'spec') && (
          <>
            <div className={`${CONTAINER} flex h-[72px] items-center gap-5`}>
              <Brand ctx={ctx} className="flex-none" />
              {header === 'marketplace' && categories.length > 0 && (
                <MenuDrawer
                  trigger={
                    <>
                      <MenuIcon width={16} height={16} /> {t.categories}
                    </>
                  }
                  triggerLabel={t.categories}
                  title={t.categories}
                  closeLabel={t.close}
                  triggerClassName="flex h-11 flex-none items-center gap-2 rounded-base bg-store-action px-3.5 text-[13.5px] font-semibold text-store-on-action hover:bg-store-action-hover"
                >
                  <CategoryList ctx={ctx} categories={categories} />
                </MenuDrawer>
              )}
              <div className="flex-1">
                <SearchForm ctx={ctx} id="store-search" />
              </div>
              {header !== 'spec' && <Language ctx={ctx} className={langButton} />}
              <AccountLink
                ctx={ctx}
                showLabel
                className={
                  dark
                    ? 'h-10 px-2.5 text-[13px] font-semibold'
                    : 'h-[42px] rounded-base border border-store-line bg-store-surface px-3 text-[13.5px] font-semibold'
                }
              />
              {ctx.config.modules.includes('cart_checkout') && (
                <CartLink label={t.cart} compact={dark} />
              )}
            </div>
            {header === 'spec' ? (
              <div className="border-t border-store-header-line">
                <div className={`${CONTAINER} flex items-center gap-4`}>
                  {nav('min-w-0 flex-1')}
                  <Language
                    ctx={ctx}
                    className="h-6 flex-none rounded-badge border border-store-header-line px-2 text-[11.5px] text-store-on-header/70"
                  />
                </div>
              </div>
            ) : (
              nav(CONTAINER)
            )}
          </>
        )}
      </div>
    </header>
  );
}

function CategoryList({ ctx, categories }: { ctx: StoreContext; categories: CategoryNode[] }) {
  const row = `flex min-h-11 items-center justify-between rounded-base px-2 text-[15px] hover:bg-store-canvas ${FOCUS}`;
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0">
      {categories.map((category) => (
        <li key={category.slug}>
          <Link href={`/categories/${category.slug}` as Route} className={row}>
            {localize(category.translations, 'name', category.name, ctx.lang)}
            <ChevronRight width={16} height={16} className="text-store-muted" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
