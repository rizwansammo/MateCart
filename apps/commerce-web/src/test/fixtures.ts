import type {
  PublicProductDetail,
  PublicProductSummary,
  PublicVariant,
  StorefrontConfig,
} from '@matecart/shared-contracts';
import { PRESETS, resolveTheme, type PresetId } from '@matecart/shared-ui';

import { messages } from '@/lib/i18n';
import type { StoreContext } from '@/lib/store-context';

/** Test data shaped exactly like decoded public API responses (Phase 06 storefront tests). */

export function variant(
  id: string,
  options: Record<string, string>,
  extra: Partial<PublicVariant> = {},
): PublicVariant {
  return {
    id,
    sku: id.toUpperCase(),
    title: Object.values(options).join(' / '),
    options: Object.entries(options).map(([attribute, option]) => ({
      attribute,
      option,
      value: option.toUpperCase(),
    })),
    kind: 'physical',
    digital_format: '',
    net_quantity: null,
    net_unit: '',
    release_date: null,
    isbn: null,
    price: { currency: 'BDT', amount: '100.0000', compare_at_amount: null },
    in_stock: true,
    ...extra,
  };
}

export function productWith(
  variants: PublicVariant[],
  extra: Partial<PublicProductDetail> = {},
): PublicProductDetail {
  return {
    id: 'p1',
    slug: 'shirt',
    name: 'Shirt',
    summary: '',
    product_type: 'apparel',
    brand: null,
    price: null,
    image: null,
    in_stock: true,
    translations: {},
    authors: [],
    description: '',
    seo_title: '',
    seo_description: '',
    categories: [],
    options: [],
    variants,
    specifications: [],
    images: [],
    apparel: null,
    grocery: null,
    electronics: null,
    book: null,
    ...extra,
  };
}

export function summary(extra: Partial<PublicProductSummary> = {}): PublicProductSummary {
  return {
    id: 's1',
    slug: 'gitanjali',
    name: 'Gitanjali',
    summary: '',
    product_type: 'book',
    brand: null,
    price: { currency: 'BDT', min_amount: '250.0000', max_amount: '450.0000' },
    image: null,
    in_stock: true,
    translations: { bn: { name: 'গীতাঞ্জলি' } },
    authors: ['Rabindranath Tagore'],
    ...extra,
  };
}

export function storeConfig(extra: Partial<StorefrontConfig['store']> = {}): StorefrontConfig {
  return {
    store: {
      name: 'Review Store',
      tagline: 'Everyday goods',
      description: '',
      announcement: 'Sample prices',
      footer_text: '',
      contact_email: '',
      contact_phone: '',
      currency: 'BDT',
      default_language: 'en',
      languages: ['en', 'bn'],
      ...extra,
    },
    preset: 'general',
    active_theme: 'general-marketplace',
    theme: {
      primary_color: null,
      accent_color: null,
      logo_url: null,
      logo_dark_url: null,
      favicon_url: null,
      show_name_with_logo: true,
    },
    modules: [],
    footer_pages: [
      {
        slug: 'shipping',
        title: 'Shipping',
        page_type: 'policy',
        translations: { bn: { title: 'শিপিং' } },
      },
    ],
    translations: { bn: { tagline: 'প্রতিদিনের পণ্য', announcement: 'নমুনা দাম' } },
    local_date: '2026-10-27',
  };
}

export function storeContext(
  preset: PresetId,
  lang = 'en',
  store: Partial<StorefrontConfig['store']> = {},
): StoreContext {
  const config = { ...storeConfig(store), preset, active_theme: PRESETS[preset].defaultTheme };
  return { config, theme: resolveTheme(preset), lang, t: messages(lang) };
}
