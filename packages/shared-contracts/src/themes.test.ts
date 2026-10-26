import { describe, expect, it } from 'vitest';

import { STORE_PRESETS } from './appearance';
import { DEFAULT_STORE_THEMES, isThemeOfPreset, STORE_THEMES, THEME_ID_PATTERN } from './themes';

describe('theme catalogue (06.2)', () => {
  it('lists unique, well-formed identifiers for known presets', () => {
    const ids = STORE_THEMES.map((theme) => theme.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const theme of STORE_THEMES) {
      expect(theme.id).toMatch(THEME_ID_PATTERN);
      expect(STORE_PRESETS).toContain(theme.preset);
    }
  });
  it('has exactly one default theme per preset, belonging to that preset', () => {
    for (const preset of STORE_PRESETS) {
      expect(STORE_THEMES.filter((t) => t.preset === preset && t.default)).toHaveLength(1);
      expect(isThemeOfPreset(DEFAULT_STORE_THEMES[preset], preset)).toBe(true);
    }
    expect(DEFAULT_STORE_THEMES).toEqual({
      fashion: 'fashion-editorial',
      grocery: 'grocery-quickshop',
      electronics: 'electronics-spec-first',
      general: 'general-marketplace',
      books: 'books-literary',
    });
  });
  it('never matches a theme to another preset or an unknown theme', () => {
    expect(isThemeOfPreset('books-literary', 'books')).toBe(true);
    expect(isThemeOfPreset('books-literary', 'fashion')).toBe(false);
    expect(isThemeOfPreset('fashion-minimal', 'fashion')).toBe(false);
  });
});
