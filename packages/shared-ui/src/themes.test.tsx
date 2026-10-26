import { DEFAULT_STORE_THEMES, STORE_THEMES } from '@matecart/shared-contracts';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  PRESET_IDS,
  PRESETS,
  THEME_IDS,
  THEMES,
  resolveTheme,
  themeVariables,
  type ThemeDefinition,
  type ThemeRegistry,
} from './presets';
import baseline from './theme-baseline.json';
import { StoreThemeScope } from './theme-ui';

/**
 * Phase 06.2 (D-069): business presets and themes are separate; the five approved designs are
 * the registered default themes, unchanged token for token.
 */

describe('theme registry', () => {
  it('registers exactly the shared catalogue, one compatible preset per theme', () => {
    expect([...THEME_IDS].sort()).toEqual(STORE_THEMES.map((t) => t.id).sort());
    for (const entry of STORE_THEMES) {
      const theme = THEMES[entry.id as keyof typeof THEMES];
      expect(theme.id).toBe(entry.id);
      expect(theme.preset).toBe(entry.preset);
      expect(theme.id).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
      expect(theme.name).not.toBe(theme.id); // display names are never identifiers
    }
    expect(new Set(THEME_IDS).size).toBe(THEME_IDS.length);
  });

  it.each(PRESET_IDS)('%s has exactly one default theme, of its own preset', (preset) => {
    const defaultTheme = PRESETS[preset].defaultTheme;
    expect(defaultTheme).toBe(DEFAULT_STORE_THEMES[preset]);
    expect(THEMES[defaultTheme].preset).toBe(preset);
    expect(STORE_THEMES.filter((t) => t.preset === preset && t.default)).toHaveLength(1);
  });

  it.each(PRESET_IDS)(
    '%s keeps its approved tokens exactly (with and without branding)',
    (preset) => {
      const expected = baseline[preset];
      expect(themeVariables(resolveTheme(preset))).toEqual(expected.variables);
      expect(
        themeVariables(resolveTheme(preset, { primaryColor: '#1F6F4A', accentColor: '#C8312B' })),
      ).toEqual(expected.branded);
      // Assigning the default explicitly is the same as not assigning one.
      expect(themeVariables(resolveTheme(preset, {}, PRESETS[preset].defaultTheme))).toEqual(
        expected.variables,
      );
    },
  );
});

describe('theme resolution', () => {
  it('uses the preset default without an assignment and the assigned theme otherwise', () => {
    const plain = resolveTheme('books');
    expect([plain.preset.id, plain.theme.id, plain.recovery]).toEqual([
      'books',
      'books-literary',
      null,
    ]);
    const assigned = resolveTheme('books', {}, 'books-literary');
    expect(assigned.theme.id).toBe('books-literary');
    expect(assigned.presentation).toBe(THEMES['books-literary'].presentation);
  });

  it.each([
    ['fashion', 'unknown-theme-id', 'fashion-editorial', 'unknown-theme'],
    ['fashion', 'books-literary', 'fashion-editorial', 'incompatible-theme'],
    ['grocery', 'general-marketplace', 'grocery-quickshop', 'incompatible-theme'],
  ] as const)(
    'a %s store assigned %s stays on its own default (%s)',
    (preset, assigned, expected, recovery) => {
      const theme = resolveTheme(preset, {}, assigned);
      expect(theme.theme.id).toBe(expected);
      expect(theme.theme.preset).toBe(preset); // never another preset's theme
      expect(theme.recovery).toBe(recovery);
    },
  );

  it('is deterministic', () => {
    const first = resolveTheme(
      'electronics',
      { primaryColor: '#1F6F4A' },
      'electronics-spec-first',
    );
    const second = resolveTheme(
      'electronics',
      { primaryColor: '#1F6F4A' },
      'electronics-spec-first',
    );
    expect(second).toEqual(first);
  });

  it('applies branding to the theme and resets to the theme’s own colours', () => {
    const branded = resolveTheme('grocery', { primaryColor: '#1F6F4A' }, 'grocery-quickshop');
    expect(branded.colors.primary).toBe('#1F6F4A');
    const reset = resolveTheme(
      'grocery',
      { primaryColor: null, accentColor: null },
      'grocery-quickshop',
    );
    expect(reset.colors.primary).toBe(THEMES['grocery-quickshop'].colors.primary);
    expect(reset.colors.action).toBe(THEMES['grocery-quickshop'].colors.action);
    // Branding never edits the registry.
    expect(THEMES['grocery-quickshop'].colors.primary).toBe('#00B050');
  });

  it('marks the business preset and the theme on the server-rendered scope', () => {
    const html = renderToString(
      <StoreThemeScope theme={resolveTheme('electronics')}>
        <p>Spec</p>
      </StoreThemeScope>,
    );
    expect(html).toContain('data-preset="electronics"');
    expect(html).toContain('data-theme="electronics-spec-first"');
  });
});

describe('a second theme for the same preset (test-only)', () => {
  // Not shipped: shows that a new Fashion design needs only a registry entry.
  const minimal: ThemeDefinition = {
    ...THEMES['fashion-editorial'],
    id: 'fashion-test-minimal',
    name: 'Fashion Minimal (test)',
    colors: { ...THEMES['fashion-editorial'].colors, canvas: '#FFFFFF', accent: '#1F3B4D' },
    presentation: {
      ...THEMES['fashion-editorial'].presentation,
      header: 'marketplace',
      card: 'marketplace',
    },
  };
  const registry: ThemeRegistry = { ...THEMES, [minimal.id]: minimal };

  it('changes presentation and tokens but not the business preset', () => {
    const editorial = resolveTheme('fashion', {}, 'fashion-editorial', registry);
    const alternative = resolveTheme('fashion', {}, 'fashion-test-minimal', registry);
    expect(alternative.preset).toEqual(editorial.preset);
    expect(alternative.theme.id).toBe('fashion-test-minimal');
    expect(alternative.presentation.header).toBe('marketplace');
    expect(editorial.presentation.header).toBe('editorial');
    expect(themeVariables(alternative)['--store-canvas']).toBe('#FFFFFF');
    // Merchant branding applies on top of whichever theme is assigned.
    expect(
      resolveTheme('fashion', { primaryColor: '#1F6F4A' }, minimal.id, registry).colors.primary,
    ).toBe('#1F6F4A');
  });

  it('cannot be used by a store of another preset', () => {
    const books = resolveTheme('books', {}, 'fashion-test-minimal', registry);
    expect([books.theme.id, books.recovery]).toEqual(['books-literary', 'incompatible-theme']);
  });

  it('is not part of the shipped registry', () => {
    expect(resolveTheme('fashion', {}, 'fashion-test-minimal').recovery).toBe('unknown-theme');
  });
});
