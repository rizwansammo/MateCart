import type { CSSProperties, ReactNode } from 'react';
import type { CardAspect, ResolvedTheme } from './presets';
import { themeVariables } from './presets';

const ASPECT: Record<CardAspect, string> = {
  '3/4': 'aspect-[3/4]',
  '1/1': 'aspect-square',
  '2/3': 'aspect-[2/3]',
};

/**
 * Applies a resolved store theme to its subtree only. Server components can render it, so the
 * first paint already has the store's preset (no flash); nothing is read from browser storage.
 */
export function StoreThemeScope({
  theme,
  className = '',
  children,
}: {
  theme: ResolvedTheme;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      data-preset={theme.preset.id}
      style={themeVariables(theme) as CSSProperties}
      className={`bg-store-canvas font-store-body text-store-ink ${className}`}
    >
      {children}
    </div>
  );
}

const displayStyle: CSSProperties = { fontWeight: 'var(--store-display-weight)' };

/**
 * A representative sample of a theme (header, headline, actions, accent, product card). It is a
 * preview of the visual system, not a storefront page: Phase 06 builds the real pages.
 */
export function ThemePreview({
  theme,
  storeName,
  tagline,
  logoUrl,
  compact = false,
}: {
  theme: ResolvedTheme;
  storeName: string;
  tagline?: string;
  logoUrl?: string | null;
  compact?: boolean;
}) {
  const { layout, label, style } = theme.preset;
  return (
    <StoreThemeScope
      theme={theme}
      className="overflow-hidden rounded-base border border-store-line shadow-card"
    >
      <div className="flex min-h-12 items-center justify-between gap-3 border-b border-store-header-line bg-store-header px-4 text-store-on-header">
        {logoUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served logo; no image optimiser
          <img src={logoUrl} alt={`${storeName} logo`} className="h-7 w-auto object-contain" />
        ) : (
          <span className="truncate font-store-brand text-lg">{storeName}</span>
        )}
        <span className="text-xs opacity-70">{compact ? style : 'Search · Account · Cart'}</span>
      </div>
      <div className={compact ? 'grid gap-3 p-4' : 'grid gap-5 p-6 sm:grid-cols-[1.4fr_1fr]'}>
        <div className="min-w-0">
          <p className="text-[11px] font-bold tracking-[0.08em] text-store-muted uppercase">
            {compact ? label : `${label} · ${style}`}
          </p>
          <p
            className={`mt-2 font-store-display leading-tight tracking-tight ${compact ? 'text-2xl' : 'text-4xl'}`}
            style={displayStyle}
          >
            {tagline || 'New arrivals, chosen with care'}
          </p>
          {!compact && (
            <p className="mt-3 text-sm leading-6 text-store-muted">
              Body text for descriptions and policies, with clear contrast on this canvas.
            </p>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="inline-flex min-h-10 items-center rounded-base bg-store-action px-4 text-sm font-semibold text-store-on-action">
              Shop now
            </span>
            <span className="inline-flex min-h-10 items-center rounded-base border border-store-line bg-store-surface px-4 text-sm font-semibold">
              Browse
            </span>
            <span className="rounded-badge bg-store-accent px-2 py-1 text-[11px] font-bold text-store-on-accent">
              −15%
            </span>
          </div>
        </div>
        <div
          className={`rounded-base border border-store-line bg-store-surface p-3 ${compact ? 'hidden' : ''}`}
        >
          <div
            role="img"
            aria-label={`Product image area, ${layout.cardAspect} ratio`}
            className={`${ASPECT[layout.cardAspect]} w-full rounded-base bg-store-canvas`}
          />
          <p className="mt-3 text-sm font-semibold">Sample product</p>
          {theme.preset.fonts.spec === 'geist-mono' && (
            <p className="font-store-spec text-xs text-store-muted">SKU NX-12 · 6.7&quot; 120 Hz</p>
          )}
          <div className="mt-2 flex items-center justify-between">
            <span className="text-sm font-bold">৳1,250</span>
            {layout.quantityStepper ? (
              <span className="inline-flex items-center gap-2 rounded-base bg-store-primary px-2 py-1 text-xs font-bold text-store-on-primary">
                − 1 +
              </span>
            ) : (
              <span className="text-xs font-semibold text-store-primary">Add</span>
            )}
          </div>
        </div>
      </div>
    </StoreThemeScope>
  );
}
