/**
 * The storefront theme catalogue (D-069): which visual themes exist and which business preset
 * each one serves. Identities only; the visual definitions live in `@matecart/shared-ui`
 * (`THEMES`) and commerce-api keeps the same list (`commerce/stores/themes.py`) to validate
 * assignments. `scripts/themes.test.mjs` checks this file against the API schema.
 *
 * Identifiers are permanent: never reuse or rename one, and never use display names. A theme
 * belongs to exactly one preset; every preset has exactly one default theme.
 */
import catalogue from './themes.json';
import { STORE_PRESETS, type StorePreset } from './appearance';

export interface ThemeCatalogueEntry {
  id: string;
  preset: StorePreset;
  default: boolean;
}

/** Lower-case words joined by hyphens, e.g. `fashion-editorial`. */
export const THEME_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const STORE_THEMES: readonly ThemeCatalogueEntry[] = catalogue.themes.map((entry) => {
  if (
    !THEME_ID_PATTERN.test(entry.id) ||
    !(STORE_PRESETS as readonly string[]).includes(entry.preset)
  ) {
    throw new Error(`Invalid theme catalogue entry: ${entry.id}`);
  }
  return { id: entry.id, preset: entry.preset as StorePreset, default: entry.default };
});

export const DEFAULT_STORE_THEMES: Readonly<Record<StorePreset, string>> = Object.fromEntries(
  STORE_PRESETS.map((preset) => {
    const defaults = STORE_THEMES.filter((t) => t.preset === preset && t.default);
    if (defaults.length !== 1 || !defaults[0]) {
      throw new Error(`The ${preset} preset needs exactly one default theme`);
    }
    return [preset, defaults[0].id];
  }),
) as Record<StorePreset, string>;

/** Whether `themeId` is a known theme of `preset`. */
export function isThemeOfPreset(themeId: string, preset: string): boolean {
  return STORE_THEMES.some((theme) => theme.id === themeId && theme.preset === preset);
}
