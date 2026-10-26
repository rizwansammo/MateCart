import type { PresentationVariant } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';
import type { SVGProps } from 'react';

/** Shared storefront building blocks (Phase 06). Colours always come from the store-* tokens. */

export const CONTAINER = 'mx-auto w-full max-w-[1320px] px-4 sm:px-6 lg:px-8';

export const FOCUS =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-store-focus';

// --- Icons (decorative; every use sits next to text or an aria-label) ----------------------------

function Icon({ d, ...props }: SVGProps<SVGSVGElement> & { d: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      width={20}
      height={20}
      {...props}
    >
      <path d={d} />
    </svg>
  );
}

export const SearchIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35" {...props} />
);
export const UserIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon
    d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"
    {...props}
  />
);
export const MenuIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon d="M3 6h18M3 12h18M3 18h18" {...props} />
);
export const CloseIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon d="M18 6 6 18M6 6l12 12" {...props} />
);
export const ChevronRight = (props: SVGProps<SVGSVGElement>) => (
  <Icon d="M9 18l6-6-6-6" {...props} />
);
export const FilterIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon d="M22 3H2l8 9.46V19l4 2v-8.54z" {...props} />
);
export const BagIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon
    d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M16 10a4 4 0 0 1-8 0"
    {...props}
  />
);

// --- Images ----------------------------------------------------------------------------------------

/**
 * An API-served image filling its (sized) parent, or a quiet placeholder with the item's initial.
 * Width and height are passed through so the browser reserves space before the file arrives.
 */
export function StoreImage({
  src,
  alt,
  width,
  height,
  label,
  priority = false,
  fit = 'cover',
}: {
  src: string | null | undefined;
  alt: string;
  width?: number | null;
  height?: number | null;
  /** Text whose first letter marks the placeholder when there is no image. */
  label: string;
  priority?: boolean;
  fit?: 'cover' | 'contain';
}) {
  if (!src) {
    return (
      <span
        role="img"
        aria-label={alt}
        className="flex h-full w-full items-center justify-center bg-store-line/40 text-store-muted"
      >
        <span aria-hidden="true" className="store-display text-4xl opacity-60">
          {label.trim().charAt(0).toUpperCase() || '·'}
        </span>
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- API-served media; no image optimiser in Phase 06
    <img
      src={src}
      alt={alt}
      width={width ?? undefined}
      height={height ?? undefined}
      loading={priority ? 'eager' : 'lazy'}
      fetchPriority={priority ? 'high' : undefined}
      decoding="async"
      className={`h-full w-full ${fit === 'cover' ? 'object-cover' : 'object-contain'}`}
    />
  );
}

// --- Navigation and text ---------------------------------------------------------------------------

export interface Crumb {
  label: string;
  href?: Route;
}

export function Breadcrumbs({ items, label }: { items: Crumb[]; label: string }) {
  return (
    <nav aria-label={label} className="text-[13px] text-store-muted">
      <ol className="flex flex-wrap items-center gap-1.5">
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`} className="flex items-center gap-1.5">
            {index > 0 && <span aria-hidden="true">/</span>}
            {item.href ? (
              <Link href={item.href} className={`hover:text-store-ink hover:underline ${FOCUS}`}>
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="font-medium text-store-ink">
                {item.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Merchant plain text (D-063): paragraphs on blank lines, line breaks kept, never HTML. */
export function PlainText({ text, className = '' }: { text: string; className?: string }) {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {paragraphs.map((paragraph, index) => (
        <p key={index} className="whitespace-pre-line">
          {paragraph}
        </p>
      ))}
    </div>
  );
}

const HEADING: Record<PresentationVariant, { row: string; title: string; link: string }> = {
  editorial: {
    row: 'flex items-baseline justify-between gap-4',
    title: 'store-display text-3xl tracking-[-0.01em] sm:text-[40px]',
    link: 'border-b border-current pb-0.5 text-[13px] font-semibold tracking-[0.08em] uppercase',
  },
  quickshop: {
    row: 'flex items-baseline justify-between gap-4',
    title: 'store-display text-[22px] tracking-[-0.02em]',
    link: 'text-[13.5px] font-bold text-store-action',
  },
  spec: {
    row: 'flex items-baseline justify-between gap-4',
    title: 'store-display text-2xl tracking-[-0.02em]',
    link: 'text-[13.5px] font-semibold text-store-primary',
  },
  marketplace: {
    row: 'flex items-baseline justify-between gap-4',
    title: 'store-display text-[22px] tracking-[-0.02em]',
    link: 'text-[13.5px] font-bold',
  },
  literary: {
    row: 'flex items-baseline justify-between gap-4 border-b border-store-line pb-3',
    title: 'store-display text-[28px] sm:text-[34px]',
    link: 'text-[13px] font-semibold text-store-primary',
  },
};

export function SectionHeading({
  variant,
  title,
  subtitle,
  href,
  linkLabel,
  id,
}: {
  /** The theme's section presentation (`presentation.sections`). */
  variant: PresentationVariant;
  title: string;
  subtitle?: string;
  href?: Route;
  linkLabel?: string;
  id?: string;
}) {
  const style = HEADING[variant];
  return (
    <div className={style.row}>
      <div className="min-w-0">
        <h2 id={id} className={`m-0 ${style.title}`}>
          {title}
        </h2>
        {subtitle && <p className="mt-1 text-sm text-store-muted">{subtitle}</p>}
      </div>
      {href && linkLabel && (
        <Link href={href} className={`shrink-0 hover:opacity-80 ${style.link} ${FOCUS}`}>
          {linkLabel}
          {variant === 'editorial' ? '' : ' →'}
        </Link>
      )}
    </div>
  );
}
