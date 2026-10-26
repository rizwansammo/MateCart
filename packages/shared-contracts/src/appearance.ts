/**
 * Runtime-validated store appearance, module and content responses (Phase 05), mirroring
 * commerce-api's OpenAPI schema. The preset id is checked against the five approved presets.
 */
import { array, boolean, number, object, string, strings } from './auth';
import { nullable, translations } from './catalog';
import type { Translations } from './catalog';

export const STORE_PRESETS = ['fashion', 'grocery', 'electronics', 'general', 'books'] as const;
export type StorePreset = (typeof STORE_PRESETS)[number];
const SECTION_TYPES = [
  'hero',
  'category_showcase',
  'collection_highlight',
  'brand_story',
  'trust_strip',
] as const;
export type SectionType = (typeof SECTION_TYPES)[number];
const HEX = /^(#[0-9A-F]{6})?$/;
const MEDIA_URL = /^\/api\/(catalog\/media|manage\/media\/assets)\/[0-9a-f-]{36}\/(file\/)?$/;

function preset(value: unknown): StorePreset {
  const text = string(value);
  if (!(STORE_PRESETS as readonly string[]).includes(text)) throw new Error('Invalid response');
  return text as StorePreset;
}
function sectionType(value: unknown): SectionType {
  const text = string(value);
  if (!(SECTION_TYPES as readonly string[]).includes(text)) throw new Error('Invalid response');
  return text as SectionType;
}
function hexOrEmpty(value: unknown): string {
  const text = string(value);
  if (!HEX.test(text)) throw new Error('Invalid response');
  return text;
}
function mediaUrl(value: unknown): string {
  const text = string(value);
  if (!MEDIA_URL.test(text)) throw new Error('Invalid response');
  return text;
}

export interface AssetRef {
  id: string;
  url: string;
  width: number | null;
  height: number | null;
}
export function assetRef(value: unknown): AssetRef {
  const o = object(value);
  return {
    id: string(o.id),
    url: mediaUrl(o.url),
    width: nullable(number)(o.width),
    height: nullable(number)(o.height),
  };
}

export interface StoreAppearance {
  preset: StorePreset;
  preset_label: string;
  store_name: string;
  can_rename: boolean;
  default_language: string;
  languages: string[];
  primary_color: string;
  accent_color: string;
  logo: AssetRef | null;
  logo_dark: AssetRef | null;
  favicon: AssetRef | null;
  tagline: string;
  description: string;
  announcement: string;
  footer_text: string;
  show_name_with_logo: boolean;
  translations: Translations;
  updated_at: string | null;
}
export function storeAppearance(value: unknown): StoreAppearance {
  const o = object(value);
  return {
    preset: preset(o.preset),
    preset_label: string(o.preset_label),
    store_name: string(o.store_name),
    can_rename: boolean(o.can_rename),
    default_language: string(o.default_language),
    languages: strings(o.languages),
    primary_color: hexOrEmpty(o.primary_color),
    accent_color: hexOrEmpty(o.accent_color),
    logo: nullable(assetRef)(o.logo),
    logo_dark: nullable(assetRef)(o.logo_dark),
    favicon: nullable(assetRef)(o.favicon),
    tagline: string(o.tagline),
    description: string(o.description),
    announcement: string(o.announcement),
    footer_text: string(o.footer_text),
    show_name_with_logo: boolean(o.show_name_with_logo),
    translations: translations(o.translations),
    updated_at: nullable(string)(o.updated_at),
  };
}

export interface StoreModuleState {
  key: string;
  name: string;
  description: string;
  phase: string;
  available: boolean;
  core: boolean;
  requires: string[];
  applicable: boolean;
  default_enabled: boolean;
  configured: boolean | null;
  enabled: boolean;
  blocked_by: string[];
}
export function storeModuleState(value: unknown): StoreModuleState {
  const o = object(value);
  return {
    key: string(o.key),
    name: string(o.name),
    description: string(o.description),
    phase: string(o.phase),
    available: boolean(o.available),
    core: boolean(o.core),
    requires: strings(o.requires),
    applicable: boolean(o.applicable),
    default_enabled: boolean(o.default_enabled),
    configured: nullable(boolean)(o.configured),
    enabled: boolean(o.enabled),
    blocked_by: strings(o.blocked_by),
  };
}

export interface ContentPage {
  id: string;
  slug: string;
  title: string;
  body: string;
  page_type: 'page' | 'policy';
  status: 'draft' | 'published';
  published_at: string | null;
  show_in_footer: boolean;
  position: number;
  translations: Translations;
  updated_at: string;
}
export function contentPage(value: unknown): ContentPage {
  const o = object(value);
  const pageType = string(o.page_type);
  const status = string(o.status);
  if (pageType !== 'page' && pageType !== 'policy') throw new Error('Invalid response');
  if (status !== 'draft' && status !== 'published') throw new Error('Invalid response');
  return {
    id: string(o.id),
    slug: string(o.slug),
    title: string(o.title),
    body: string(o.body),
    page_type: pageType,
    status,
    published_at: nullable(string)(o.published_at),
    show_in_footer: boolean(o.show_in_footer),
    position: number(o.position),
    translations: translations(o.translations),
    updated_at: string(o.updated_at),
  };
}

export interface HomeSection {
  id: string;
  section_type: SectionType;
  position: number;
  is_enabled: boolean;
  title: string;
  subtitle: string;
  body: string;
  image: AssetRef | null;
  collection: { id: string; name: string; slug: string } | null;
  cta_label: string;
  cta_url: string;
  translations: Translations;
}
export function homeSection(value: unknown): HomeSection {
  const o = object(value);
  return {
    id: string(o.id),
    section_type: sectionType(o.section_type),
    position: number(o.position),
    is_enabled: boolean(o.is_enabled),
    title: string(o.title),
    subtitle: string(o.subtitle),
    body: string(o.body),
    image: nullable(assetRef)(o.image),
    collection: nullable((entry) => {
      const c = object(entry);
      return { id: string(c.id), name: string(c.name), slug: string(c.slug) };
    })(o.collection),
    cta_label: string(o.cta_label),
    cta_url: string(o.cta_url),
    translations: translations(o.translations),
  };
}

/** An enabled homepage section as the public storefront receives it (/api/storefront/home/). */
export interface StorefrontSection {
  section_type: SectionType;
  title: string;
  subtitle: string;
  body: string;
  image_url: string | null;
  collection: { name: string; slug: string } | null;
  cta_label: string;
  cta_url: string;
  translations: Translations;
}
export function storefrontSection(value: unknown): StorefrontSection {
  const o = object(value);
  return {
    section_type: sectionType(o.section_type),
    title: string(o.title),
    subtitle: string(o.subtitle),
    body: string(o.body),
    image_url: nullable(mediaUrl)(o.image_url),
    collection: nullable((entry) => {
      const c = object(entry);
      return { name: string(c.name), slug: string(c.slug) };
    })(o.collection),
    cta_label: string(o.cta_label),
    cta_url: string(o.cta_url),
    translations: translations(o.translations),
  };
}

/** A published page (/api/storefront/pages/<slug>/). Drafts are never returned. */
export interface StorefrontPage {
  slug: string;
  title: string;
  body: string;
  page_type: 'page' | 'policy';
  published_at: string;
  translations: Translations;
}
export function storefrontPage(value: unknown): StorefrontPage {
  const o = object(value);
  const pageType = string(o.page_type);
  if (pageType !== 'page' && pageType !== 'policy') throw new Error('Invalid response');
  return {
    slug: string(o.slug),
    title: string(o.title),
    body: string(o.body),
    page_type: pageType,
    published_at: string(o.published_at),
    translations: translations(o.translations),
  };
}

export interface StorefrontConfig {
  store: {
    name: string;
    tagline: string;
    description: string;
    announcement: string;
    footer_text: string;
    contact_email: string;
    contact_phone: string;
    currency: string;
    default_language: string;
    languages: string[];
  };
  preset: StorePreset;
  theme: {
    primary_color: string | null;
    accent_color: string | null;
    logo_url: string | null;
    logo_dark_url: string | null;
    favicon_url: string | null;
    show_name_with_logo: boolean;
  };
  modules: string[];
  footer_pages: { slug: string; title: string; page_type: string; translations: Translations }[];
  translations: Translations;
}
export function storefrontConfig(value: unknown): StorefrontConfig {
  const o = object(value);
  const store = object(o.store);
  const theme = object(o.theme);
  return {
    store: {
      name: string(store.name),
      tagline: string(store.tagline),
      description: string(store.description),
      announcement: string(store.announcement),
      footer_text: string(store.footer_text),
      contact_email: string(store.contact_email),
      contact_phone: string(store.contact_phone),
      currency: string(store.currency),
      default_language: string(store.default_language),
      languages: strings(store.languages),
    },
    preset: preset(o.preset),
    theme: {
      primary_color: nullable(hexOrEmpty)(theme.primary_color),
      accent_color: nullable(hexOrEmpty)(theme.accent_color),
      logo_url: nullable(mediaUrl)(theme.logo_url),
      logo_dark_url: nullable(mediaUrl)(theme.logo_dark_url),
      favicon_url: nullable(mediaUrl)(theme.favicon_url),
      show_name_with_logo: boolean(theme.show_name_with_logo),
    },
    modules: strings(o.modules),
    footer_pages: array(o.footer_pages, (entry) => {
      const p = object(entry);
      return {
        slug: string(p.slug),
        title: string(p.title),
        page_type: string(p.page_type),
        translations: translations(p.translations),
      };
    }),
    translations: translations(o.translations),
  };
}
