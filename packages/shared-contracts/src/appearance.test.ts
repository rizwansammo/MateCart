import { describe, expect, it } from 'vitest';
import { homeSection, storeAppearance, storeModuleState, storefrontConfig } from './appearance';

const config = {
  store: {
    name: 'Rahim Fashion',
    tagline: 'Woven slowly',
    description: '',
    announcement: '',
    footer_text: '',
    contact_email: 'hello@example.com',
    contact_phone: '',
    currency: 'BDT',
    default_language: 'en',
    languages: ['en', 'bn'],
  },
  preset: 'fashion',
  theme: {
    primary_color: '#1F6F4A',
    accent_color: null,
    logo_url: '/api/catalog/media/01a17556-19cd-765f-9e28-700044901b91/',
    logo_dark_url: null,
    favicon_url: null,
    show_name_with_logo: true,
  },
  modules: ['catalog', 'size_guides'],
  footer_pages: [{ slug: 'returns', title: 'Returns', page_type: 'policy' }],
  translations: { bn: { tagline: 'ধীরে বোনা' } },
};

describe('appearance response boundaries', () => {
  it('accepts a public storefront configuration', () => {
    const decoded = storefrontConfig(config);
    expect(decoded.preset).toBe('fashion');
    expect(decoded.theme.primary_color).toBe('#1F6F4A');
    expect(decoded.translations.bn?.tagline).toBe('ধীরে বোনা');
  });
  it('rejects unknown presets', () => {
    expect(() => storefrontConfig({ ...config, preset: 'casino' })).toThrow();
  });
  it('rejects colours that are not #RRGGBB', () => {
    const theme = { ...config.theme, primary_color: 'red;background:url(x)' };
    expect(() => storefrontConfig({ ...config, theme })).toThrow();
  });
  it('rejects logo URLs outside the API', () => {
    const theme = { ...config.theme, logo_url: 'https://tracker.invalid/logo.png' };
    expect(() => storefrontConfig({ ...config, theme })).toThrow();
  });
  it('decodes merchant appearance, modules and sections', () => {
    const appearance = storeAppearance({
      preset: 'books',
      preset_label: 'Bookstore & Booksellers',
      store_name: 'Northlight',
      can_rename: false,
      default_language: 'en',
      languages: ['en'],
      primary_color: '',
      accent_color: '',
      logo: null,
      logo_dark: null,
      favicon: null,
      tagline: '',
      description: '',
      announcement: '',
      footer_text: '',
      show_name_with_logo: true,
      translations: {},
      updated_at: null,
    });
    expect(appearance.preset).toBe('books');
    const state = storeModuleState({
      key: 'cart_checkout',
      name: 'Cart and checkout',
      description: '',
      phase: '07',
      available: false,
      core: false,
      requires: [],
      applicable: true,
      default_enabled: false,
      configured: null,
      enabled: false,
      blocked_by: [],
    });
    expect(state.available).toBe(false);
    expect(() =>
      homeSection({
        id: 's1',
        section_type: 'popup_ad',
        position: 0,
        is_enabled: true,
        title: '',
        subtitle: '',
        body: '',
        image: null,
        collection: null,
        cta_label: '',
        cta_url: '',
        translations: {},
      }),
    ).toThrow();
  });
});
