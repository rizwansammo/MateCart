/**
 * Business presets and storefront themes (D-061, D-069).
 *
 * A business preset (Fashion, Grocery, Electronics, General, Books) decides what a store can
 * sell and which modules it has; that lives in commerce-api. A theme is a visual design for one
 * preset: colours, typography, layout preferences and the presentation variant of each storefront
 * region. Themes never change functionality: every theme of a preset uses the same components,
 * data and business rules (D-069). Merchant branding then overrides a few tokens of the theme.
 *
 *   business preset + assigned theme + merchant branding  ->  resolved storefront presentation
 *
 * The five registered themes are the approved prototype designs (MateCart-UI: DesignSystem.tsx,
 * Storefront.tsx, ProductCard.tsx). Their identifiers come from the shared catalogue
 * (`@matecart/shared-contracts` themes.json); NetaMate assigns themes, merchants never do.
 * `resolveTheme` is deterministic and never lets an unknown or other-preset theme through.
 */

import { DEFAULT_STORE_THEMES } from '@matecart/shared-contracts';

export const PRESET_IDS = ['fashion', 'grocery', 'electronics', 'general', 'books'] as const;
export type PresetId = (typeof PRESET_IDS)[number];

export type FontRole = 'geist' | 'geist-mono' | 'instrument-serif' | 'newsreader';
export type CardAspect = '3/4' | '1/1' | '2/3';
export type Density = 'compact' | 'standard' | 'comfortable';

export interface ThemeColors {
  canvas: string;
  surface: string;
  ink: string;
  muted: string;
  line: string;
  primary: string;
  accent: string;
  /** Primary call-to-action background (prototype `ctaBg`). */
  action: string;
  header: string;
  onHeader: string;
  headerLine: string;
}
/** Earlier name of ThemeColors (Phase 05). */
export type PresetColors = ThemeColors;

/**
 * How a theme presents each storefront region. The storefront components implement these
 * variants once, with shared behaviour; a new theme picks variants (and later, new variants are
 * added to the same components). The current values name the five approved designs.
 */
export type PresentationVariant = 'editorial' | 'quickshop' | 'spec' | 'marketplace' | 'literary';
export type HeroStyle = 'editorial' | 'promo' | 'spotlight' | 'carousel' | 'featured-title';

export interface ThemePresentation {
  /** Header composition (brand position, search style, category navigation). */
  header: PresentationVariant;
  /** Homepage hero layout. */
  hero: HeroStyle;
  /** Product cards, grids and page density. */
  card: PresentationVariant;
  /** Homepage sections, section headings and listing titles. */
  sections: PresentationVariant;
  /** Product page gallery and title block. */
  product: PresentationVariant;
}

/** A business preset as the storefront needs it. */
export interface BusinessPreset {
  id: PresetId;
  label: string;
  /** The theme a store of this preset uses until NetaMate assigns another. */
  defaultTheme: ThemeId;
}

export interface ThemeDefinition {
  /** Permanent identifier (never a display name; never reused). */
  id: string;
  /** The one business preset this theme serves. */
  preset: PresetId;
  name: string;
  style: string;
  description: string;
  /** Increases when the design changes in a release; identity stays the same. */
  revision: number;
  /** The identity triple shown in the design system. */
  swatches: readonly [string, string, string];
  colors: ThemeColors;
  fonts: {
    display: FontRole;
    displayWeight: number;
    body: FontRole;
    brand: FontRole;
    spec: FontRole;
  };
  layout: {
    cardAspect: CardAspect;
    density: Density;
    columns: number;
    quantityStepper: boolean;
    darkHeader: boolean;
  };
  presentation: ThemePresentation;
}

export const THEME_IDS = [
  'fashion-editorial',
  'grocery-quickshop',
  'electronics-spec-first',
  'general-marketplace',
  'books-literary',
] as const;
export type ThemeId = (typeof THEME_IDS)[number];

/** Theme definitions by identifier. Tests may pass their own registry to `resolveTheme`. */
export type ThemeRegistry = Readonly<Record<string, ThemeDefinition>>;

export const THEMES: Readonly<Record<ThemeId, ThemeDefinition>> = {
  'fashion-editorial': {
    id: 'fashion-editorial',
    preset: 'fashion',
    name: 'Fashion Editorial',
    style: 'Editorial',
    description: 'Ivory canvas, serif headlines and tall 3:4 imagery.',
    revision: 1,
    swatches: ['#FBF8F3', '#2A2620', '#9C4A2B'],
    colors: {
      canvas: '#FBF8F3',
      surface: '#FFFFFF',
      ink: '#2A2620',
      muted: '#6B6258',
      line: '#E2D6C6',
      primary: '#2A2620',
      accent: '#9C4A2B',
      action: '#2A2620',
      header: '#FBF8F3',
      onHeader: '#2A2620',
      headerLine: '#E2D6C6',
    },
    fonts: {
      display: 'instrument-serif',
      displayWeight: 400,
      body: 'geist',
      brand: 'instrument-serif',
      spec: 'geist',
    },
    layout: {
      cardAspect: '3/4',
      density: 'comfortable',
      columns: 4,
      quantityStepper: false,
      darkHeader: false,
    },
    presentation: {
      header: 'editorial',
      hero: 'editorial',
      card: 'editorial',
      sections: 'editorial',
      product: 'editorial',
    },
  },
  'grocery-quickshop': {
    id: 'grocery-quickshop',
    preset: 'grocery',
    name: 'Grocery Quick Shop',
    style: 'Quick shop',
    description: 'Fresh green, a dense grid and quantity steppers on cards.',
    revision: 1,
    swatches: ['#F6F8F4', '#00B050', '#FFF3DC'],
    colors: {
      canvas: '#F6F8F4',
      surface: '#FFFFFF',
      ink: '#0D0F0E',
      muted: '#5E6662',
      line: '#E4E7E5',
      primary: '#00B050',
      accent: '#FFF3DC',
      // Green actions with white text need the deeper green for AA contrast.
      action: '#00843C',
      header: '#FFFFFF',
      onHeader: '#0D0F0E',
      headerLine: '#E4E7E5',
    },
    fonts: { display: 'geist', displayWeight: 800, body: 'geist', brand: 'geist', spec: 'geist' },
    layout: {
      cardAspect: '1/1',
      density: 'compact',
      columns: 6,
      quantityStepper: true,
      darkHeader: false,
    },
    presentation: {
      header: 'quickshop',
      hero: 'promo',
      card: 'quickshop',
      sections: 'quickshop',
      product: 'quickshop',
    },
  },
  'electronics-spec-first': {
    id: 'electronics-spec-first',
    preset: 'electronics',
    name: 'Electronics Spec-first',
    style: 'Spec-first',
    description: 'Dark header, electric blue and monospaced specifications.',
    revision: 1,
    swatches: ['#0B0F14', '#2F6BFF', '#F3F5F8'],
    colors: {
      canvas: '#F3F5F8',
      surface: '#FFFFFF',
      ink: '#0B0F14',
      muted: '#5B6573',
      line: '#DDE2E9',
      primary: '#2F6BFF',
      accent: '#F3F5F8',
      action: '#0B0F14',
      header: '#0B0F14',
      onHeader: '#FFFFFF',
      headerLine: '#1C232D',
    },
    fonts: {
      display: 'geist',
      displayWeight: 700,
      body: 'geist',
      brand: 'geist-mono',
      spec: 'geist-mono',
    },
    layout: {
      cardAspect: '1/1',
      density: 'standard',
      columns: 4,
      quantityStepper: false,
      darkHeader: true,
    },
    presentation: {
      header: 'spec',
      hero: 'spotlight',
      card: 'spec',
      sections: 'spec',
      product: 'spec',
    },
  },
  'general-marketplace': {
    id: 'general-marketplace',
    preset: 'general',
    name: 'General Marketplace',
    style: 'Marketplace',
    description: 'Neutral base, red deals and category carousels.',
    revision: 1,
    swatches: ['#FAFAF9', '#0D0F0E', '#C8312B'],
    colors: {
      canvas: '#FAFAF9',
      surface: '#FFFFFF',
      ink: '#0D0F0E',
      muted: '#5E6662',
      line: '#E4E7E5',
      primary: '#0D0F0E',
      accent: '#C8312B',
      action: '#0D0F0E',
      header: '#FFFFFF',
      onHeader: '#0D0F0E',
      headerLine: '#E4E7E5',
    },
    fonts: { display: 'geist', displayWeight: 700, body: 'geist', brand: 'geist', spec: 'geist' },
    layout: {
      cardAspect: '1/1',
      density: 'standard',
      columns: 5,
      quantityStepper: false,
      darkHeader: false,
    },
    presentation: {
      header: 'marketplace',
      hero: 'carousel',
      card: 'marketplace',
      sections: 'marketplace',
      product: 'marketplace',
    },
  },
  'books-literary': {
    id: 'books-literary',
    preset: 'books',
    name: 'Books Literary',
    style: 'Literary',
    description: 'Paper canvas, oxblood, Newsreader serif and cover grids.',
    revision: 1,
    swatches: ['#F7F2E8', '#6E1F2A', '#1B1A17'],
    colors: {
      canvas: '#F7F2E8',
      surface: '#FFFDF8',
      ink: '#1B1A17',
      muted: '#6B6457',
      line: '#E5DCCB',
      primary: '#6E1F2A',
      accent: '#1B1A17',
      action: '#1B1A17',
      header: '#F7F2E8',
      onHeader: '#1B1A17',
      headerLine: '#E5DCCB',
    },
    fonts: {
      display: 'newsreader',
      displayWeight: 400,
      body: 'geist',
      brand: 'newsreader',
      spec: 'geist',
    },
    layout: {
      cardAspect: '2/3',
      density: 'comfortable',
      columns: 5,
      quantityStepper: false,
      darkHeader: false,
    },
    presentation: {
      header: 'literary',
      hero: 'featured-title',
      card: 'literary',
      sections: 'literary',
      product: 'literary',
    },
  },
};

function isThemeId(value: string): value is ThemeId {
  return (THEME_IDS as readonly string[]).includes(value);
}

function defaultThemeOf(preset: PresetId): ThemeId {
  const id = DEFAULT_STORE_THEMES[preset];
  if (!isThemeId(id)) throw new Error(`No registered default theme for the ${preset} preset`);
  return id;
}

const PRESET_LABELS: Readonly<Record<PresetId, string>> = {
  fashion: 'Fashion & Apparel',
  grocery: 'Grocery & Essentials',
  electronics: 'Electronics & Gadgets',
  general: 'General / Multi-category',
  books: 'Bookstore & Booksellers',
};

/** The business presets with their labels and default themes. */
export const PRESETS: Readonly<Record<PresetId, BusinessPreset>> = Object.fromEntries(
  PRESET_IDS.map((id) => [id, { id, label: PRESET_LABELS[id], defaultTheme: defaultThemeOf(id) }]),
) as Record<PresetId, BusinessPreset>;

/** CSS font stacks for each role. Every var() has a fallback (see theme.css). */
export const FONT_STACKS: Readonly<Record<FontRole, string>> = {
  geist:
    'var(--font-geist, system-ui), var(--font-hind-siliguri, system-ui), system-ui, sans-serif',
  'geist-mono': 'var(--font-geist-mono, ui-monospace), ui-monospace, monospace',
  'instrument-serif':
    'var(--font-instrument-serif, Georgia), var(--font-hind-siliguri, serif), Georgia, serif',
  newsreader: 'var(--font-newsreader, Georgia), var(--font-hind-siliguri, serif), Georgia, serif',
};

// --- Colour maths (same WCAG formulas as commerce-api stores/colors.py) -------------------------

const HEX = /^#[0-9A-F]{6}$/;
export const WHITE = '#FFFFFF';
export const INK = '#0D0F0E';
export const MIN_TEXT_CONTRAST = 4.5;

export function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && HEX.test(value.trim().toUpperCase());
}

function channel(value: number): number {
  const srgb = value / 255;
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
}

export function luminance(color: string): number {
  const hex = color.toUpperCase();
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.2126 * channel(r ?? 0) + 0.7152 * channel(g ?? 0) + 0.0722 * channel(b ?? 0);
}

export function contrast(first: string, second: string): number {
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05);
}

/** White or ink, whichever reads better on `background`. */
export function onColor(background: string): string {
  return contrast(background, WHITE) >= contrast(background, INK) ? WHITE : INK;
}

export function isReadable(background: string): boolean {
  return contrast(background, onColor(background)) >= MIN_TEXT_CONTRAST;
}

/** Shift a colour towards white (`amount` > 0) or black (< 0), in sRGB. */
function shade(color: string, amount: number): string {
  const target = amount > 0 ? 255 : 0;
  const weight = Math.abs(amount);
  const parts = [1, 3, 5].map((i) => {
    const value = parseInt(color.slice(i, i + 2), 16);
    return Math.round(value + (target - value) * weight)
      .toString(16)
      .padStart(2, '0');
  });
  return `#${parts.join('')}`.toUpperCase();
}

// --- Theme resolution -----------------------------------------------------------------------------

export interface ThemeOverrides {
  primaryColor?: string | null;
  accentColor?: string | null;
}

/** Why the assigned theme was not used (for operational logs; never shown to customers). */
export type ThemeRecovery = 'unknown-theme' | 'incompatible-theme';

export interface ResolvedTheme {
  /** The store's business preset. */
  preset: BusinessPreset;
  /** The theme in effect (assigned, or the preset's default). */
  theme: ThemeDefinition;
  /** Shortcut to `theme.presentation`. */
  presentation: ThemePresentation;
  colors: ThemeColors & {
    onPrimary: string;
    onAccent: string;
    onAction: string;
    actionHover: string;
    focus: string;
  };
  /** Set when the assigned theme could not be used and the preset default was used instead. */
  recovery: ThemeRecovery | null;
}

function validOverride(value: string | null | undefined): string | null {
  if (!isHexColor(value)) return null;
  const color = value.trim().toUpperCase();
  return isReadable(color) ? color : null;
}

export function isPresetId(value: unknown): value is PresetId {
  return typeof value === 'string' && (PRESET_IDS as readonly string[]).includes(value);
}

/**
 * The effective theme: the assigned theme when it exists and belongs to the store's preset,
 * otherwise the preset's default (with `recovery` set, never another preset's theme), plus valid
 * brand colour overrides. Unknown presets fall back to General.
 */
export function resolveTheme(
  preset: unknown,
  overrides: ThemeOverrides = {},
  themeId?: string | null,
  registry: ThemeRegistry = THEMES,
): ResolvedTheme {
  const business = PRESETS[isPresetId(preset) ? preset : 'general'];
  let definition: ThemeDefinition = THEMES[business.defaultTheme];
  let recovery: ThemeRecovery | null = null;
  if (themeId) {
    const assigned = registry[themeId];
    if (!assigned) recovery = 'unknown-theme';
    else if (assigned.preset !== business.id) recovery = 'incompatible-theme';
    else definition = assigned;
  }
  const base = definition.colors;
  const primary = validOverride(overrides.primaryColor) ?? base.primary;
  const accent = validOverride(overrides.accentColor) ?? base.accent;
  // A brand primary colour also becomes the call-to-action colour.
  const action = primary === base.primary ? base.action : primary;
  const actionHover = shade(action, luminance(action) < 0.1 ? 0.14 : -0.12);
  const focus = contrast(primary, base.canvas) >= 3 ? primary : base.ink;
  return {
    preset: business,
    theme: definition,
    presentation: definition.presentation,
    colors: {
      ...base,
      primary,
      accent,
      action,
      onPrimary: onColor(primary),
      onAccent: onColor(accent),
      onAction: onColor(action),
      actionHover,
      focus,
    },
    recovery,
  };
}

/** CSS custom properties for a theme scope (read by the `store-*` tokens in theme.css). */
export function themeVariables(theme: ResolvedTheme): Record<string, string> {
  const c = theme.colors;
  const f = theme.theme.fonts;
  return {
    '--store-canvas': c.canvas,
    '--store-surface': c.surface,
    '--store-ink': c.ink,
    '--store-muted': c.muted,
    '--store-line': c.line,
    '--store-primary': c.primary,
    '--store-on-primary': c.onPrimary,
    '--store-accent': c.accent,
    '--store-on-accent': c.onAccent,
    '--store-action': c.action,
    '--store-on-action': c.onAction,
    '--store-action-hover': c.actionHover,
    '--store-focus': c.focus,
    '--store-header': c.header,
    '--store-on-header': c.onHeader,
    '--store-header-line': c.headerLine,
    '--store-font-display': FONT_STACKS[f.display],
    '--store-font-body': FONT_STACKS[f.body],
    '--store-font-brand': FONT_STACKS[f.brand],
    '--store-font-spec': FONT_STACKS[f.spec],
    '--store-display-weight': String(f.displayWeight),
  };
}
