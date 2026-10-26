/**
 * The five approved preset identities (D-061), from the read-only prototype (MateCart-UI:
 * DesignSystem.tsx, Storefront.tsx, ProductCard.tsx). One engine, five presentations: these are
 * visual definitions only. Business defaults (modules, homepage sections, product types) live in
 * commerce-api.
 *
 * `resolveTheme` turns a store's preset and validated brand overrides into the effective theme;
 * `themeVariables` turns that into the CSS custom properties the `store-*` Tailwind tokens read
 * (theme.css). Unknown presets fall back to General; unreadable or malformed colours are ignored.
 */

export const PRESET_IDS = ['fashion', 'grocery', 'electronics', 'general', 'books'] as const;
export type PresetId = (typeof PRESET_IDS)[number];

export type FontRole = 'geist' | 'geist-mono' | 'instrument-serif' | 'newsreader';
export type CardAspect = '3/4' | '1/1' | '2/3';
export type Density = 'compact' | 'standard' | 'comfortable';

export interface PresetColors {
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

export interface PresetDefinition {
  id: PresetId;
  label: string;
  style: string;
  description: string;
  /** The identity triple shown in the design system. */
  swatches: readonly [string, string, string];
  colors: PresetColors;
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
    heroStyle: 'editorial' | 'promo' | 'spotlight' | 'carousel' | 'featured-title';
  };
}

export const PRESETS: Readonly<Record<PresetId, PresetDefinition>> = {
  fashion: {
    id: 'fashion',
    label: 'Fashion & Apparel',
    style: 'Editorial',
    description: 'Ivory canvas, serif headlines and tall 3:4 imagery.',
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
      heroStyle: 'editorial',
    },
  },
  grocery: {
    id: 'grocery',
    label: 'Grocery & Essentials',
    style: 'Quick shop',
    description: 'Fresh green, a dense grid and quantity steppers on cards.',
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
      heroStyle: 'promo',
    },
  },
  electronics: {
    id: 'electronics',
    label: 'Electronics & Gadgets',
    style: 'Spec-first',
    description: 'Dark header, electric blue and monospaced specifications.',
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
      heroStyle: 'spotlight',
    },
  },
  general: {
    id: 'general',
    label: 'General / Multi-category',
    style: 'Marketplace',
    description: 'Neutral base, red deals and category carousels.',
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
      heroStyle: 'carousel',
    },
  },
  books: {
    id: 'books',
    label: 'Bookstore & Booksellers',
    style: 'Literary',
    description: 'Paper canvas, oxblood, Newsreader serif and cover grids.',
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
      heroStyle: 'featured-title',
    },
  },
};

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

export interface ResolvedTheme {
  preset: PresetDefinition;
  colors: PresetColors & {
    onPrimary: string;
    onAccent: string;
    onAction: string;
    actionHover: string;
    focus: string;
  };
}

function validOverride(value: string | null | undefined): string | null {
  if (!isHexColor(value)) return null;
  const color = value.trim().toUpperCase();
  return isReadable(color) ? color : null;
}

export function isPresetId(value: unknown): value is PresetId {
  return typeof value === 'string' && (PRESET_IDS as readonly string[]).includes(value);
}

/** The effective theme: preset defaults plus valid overrides; General when the preset is unknown. */
export function resolveTheme(preset: unknown, overrides: ThemeOverrides = {}): ResolvedTheme {
  const definition = PRESETS[isPresetId(preset) ? preset : 'general'];
  const base = definition.colors;
  const primary = validOverride(overrides.primaryColor) ?? base.primary;
  const accent = validOverride(overrides.accentColor) ?? base.accent;
  // A brand primary colour also becomes the call-to-action colour.
  const action = primary === base.primary ? base.action : primary;
  const actionHover = shade(action, luminance(action) < 0.1 ? 0.14 : -0.12);
  const focus = contrast(primary, base.canvas) >= 3 ? primary : base.ink;
  return {
    preset: definition,
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
  };
}

/** CSS custom properties for a theme scope (read by the `store-*` tokens in theme.css). */
export function themeVariables(theme: ResolvedTheme): Record<string, string> {
  const c = theme.colors;
  const f = theme.preset.fonts;
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
