/**
 * Runtime-validated catalogue and inventory responses, mirroring the committed OpenAPI schema.
 * Money and quantities are decimal strings ("1250.0000", "5.000"), never JS numbers, so no
 * floating-point rounding happens in the browser.
 */
import { array, boolean, number, object, string, strings, type Decoder } from './auth';

const DECIMAL = /^-?\d+(\.\d+)?$/;
const MEDIA_URL = /^\/api\/(catalog\/media|manage\/media\/assets)\/[0-9a-f-]{36}\/(file\/)?$/;

export function decimal(value: unknown): string {
  const text = string(value);
  if (!DECIMAL.test(text)) throw new Error('Invalid response');
  return text;
}
export function nullable<T>(decode: Decoder<T>): Decoder<T | null> {
  return (value) => (value === null ? null : decode(value));
}
function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value) => {
    const text = string(value);
    if (!(values as readonly string[]).includes(text)) throw new Error('Invalid response');
    return text as T;
  };
}
/** Only same-origin API paths: a response can never point the page at another host. */
function mediaUrl(value: unknown): string {
  const text = string(value);
  if (!MEDIA_URL.test(text)) throw new Error('Invalid response');
  return text;
}

export const PRODUCT_TYPES = ['standard', 'apparel', 'grocery', 'electronics', 'book'] as const;
export type ProductType = (typeof PRODUCT_TYPES)[number];
export const MOVEMENT_TYPES = [
  'receipt',
  'adjustment',
  'damaged',
  'lost',
  'returned',
  'transfer_out',
  'transfer_in',
  'reserved',
  'released',
  'fulfilled',
  'reversal',
] as const;
export type MovementType = (typeof MOVEMENT_TYPES)[number];

export type Translations = Record<string, Record<string, string>>;
export function translations(value: unknown): Translations {
  const result: Translations = {};
  for (const [language, entry] of Object.entries(object(value))) {
    const texts: Record<string, string> = {};
    for (const [key, text] of Object.entries(object(entry))) texts[key] = string(text);
    result[language] = texts;
  }
  return result;
}

export interface Page<T> {
  count: number;
  page: number;
  page_size: number;
  results: T[];
}
export function page<T>(decode: Decoder<T>): Decoder<Page<T>> {
  return (value) => {
    const o = object(value);
    return {
      count: number(o.count),
      page: number(o.page),
      page_size: number(o.page_size),
      results: array(o.results, decode),
    };
  };
}

// --- Public catalogue (/api/catalog/) -------------------------------------------------------------

export interface PriceRange {
  currency: string;
  min_amount: string;
  max_amount: string;
}
export function priceRange(value: unknown): PriceRange {
  const o = object(value);
  return {
    currency: string(o.currency),
    min_amount: decimal(o.min_amount),
    max_amount: decimal(o.max_amount),
  };
}
export interface PublicPrice {
  currency: string;
  amount: string;
  compare_at_amount: string | null;
}
export function publicPrice(value: unknown): PublicPrice {
  const o = object(value);
  return {
    currency: string(o.currency),
    amount: decimal(o.amount),
    compare_at_amount: nullable(decimal)(o.compare_at_amount),
  };
}
export interface PublicImage {
  url: string;
  alt_text: string;
  width: number | null;
  height: number | null;
  variant: string | null;
}
export function publicImage(value: unknown): PublicImage {
  const o = object(value);
  return {
    url: mediaUrl(o.url),
    alt_text: string(o.alt_text),
    width: nullable(number)(o.width),
    height: nullable(number)(o.height),
    variant: nullable(string)(o.variant),
  };
}
export interface PublicProductSummary {
  id: string;
  slug: string;
  name: string;
  summary: string;
  product_type: ProductType;
  brand: { name: string; slug: string } | null;
  price: PriceRange | null;
  image: PublicImage | null;
  in_stock: boolean;
  translations: Translations;
}
export function publicProductSummary(value: unknown): PublicProductSummary {
  const o = object(value);
  return {
    id: string(o.id),
    slug: string(o.slug),
    name: string(o.name),
    summary: string(o.summary),
    product_type: oneOf(PRODUCT_TYPES)(o.product_type),
    brand: nullable((brand) => {
      const b = object(brand);
      return { name: string(b.name), slug: string(b.slug) };
    })(o.brand),
    price: nullable(priceRange)(o.price),
    image: nullable(publicImage)(o.image),
    in_stock: boolean(o.in_stock),
    translations: translations(o.translations),
  };
}
export interface PublicVariant {
  id: string;
  sku: string;
  title: string;
  options: { attribute: string; option: string; value: string }[];
  kind: 'physical' | 'digital';
  digital_format: string;
  net_quantity: string | null;
  net_unit: string;
  release_date: string | null;
  price: PublicPrice | null;
  in_stock: boolean;
}
export function publicVariant(value: unknown): PublicVariant {
  const o = object(value);
  return {
    id: string(o.id),
    sku: string(o.sku),
    title: string(o.title),
    options: array(o.options, (entry) => {
      const e = object(entry);
      return { attribute: string(e.attribute), option: string(e.option), value: string(e.value) };
    }),
    kind: oneOf(['physical', 'digital'] as const)(o.kind),
    digital_format: string(o.digital_format),
    net_quantity: nullable(decimal)(o.net_quantity),
    net_unit: string(o.net_unit),
    release_date: nullable(string)(o.release_date),
    price: nullable(publicPrice)(o.price),
    in_stock: boolean(o.in_stock),
  };
}
export interface Specification {
  attribute: string;
  name: string;
  value_type: string;
  unit: string;
  value: string | boolean;
  display: string;
}
export function specification(value: unknown): Specification {
  const o = object(value);
  return {
    attribute: string(o.attribute),
    name: string(o.name),
    value_type: string(o.value_type),
    unit: string(o.unit),
    value: typeof o.value === 'boolean' ? o.value : string(o.value),
    display: string(o.display),
  };
}
export interface PublicProductDetail extends PublicProductSummary {
  description: string;
  seo_title: string;
  seo_description: string;
  categories: { name: string; slug: string; is_primary: boolean }[];
  options: {
    code: string;
    name: string;
    values: { code: string; value: string; swatch: string }[];
  }[];
  variants: PublicVariant[];
  specifications: Specification[];
  images: PublicImage[];
  book: {
    authors: string[];
    publisher: string;
    language: string;
    page_count: number | null;
  } | null;
}
export function publicProductDetail(value: unknown): PublicProductDetail {
  const o = object(value);
  return {
    ...publicProductSummary(value),
    description: string(o.description),
    seo_title: string(o.seo_title),
    seo_description: string(o.seo_description),
    categories: array(o.categories, (entry) => {
      const c = object(entry);
      return { name: string(c.name), slug: string(c.slug), is_primary: boolean(c.is_primary) };
    }),
    options: array(o.options, (entry) => {
      const option = object(entry);
      return {
        code: string(option.code),
        name: string(option.name),
        values: array(option.values, (item) => {
          const v = object(item);
          return { code: string(v.code), value: string(v.value), swatch: string(v.swatch) };
        }),
      };
    }),
    variants: array(o.variants, publicVariant),
    specifications: array(o.specifications, specification),
    images: array(o.images, publicImage),
    book: nullable((entry) => {
      const b = object(entry);
      return {
        authors: strings(b.authors),
        publisher: string(b.publisher),
        language: string(b.language),
        page_count: nullable(number)(b.page_count),
      };
    })(o.book),
  };
}
export interface CategoryNode {
  name: string;
  slug: string;
  description: string;
  image_url: string | null;
  translations: Translations;
  children: CategoryNode[];
}
export function categoryNode(value: unknown): CategoryNode {
  const o = object(value);
  return {
    name: string(o.name),
    slug: string(o.slug),
    description: string(o.description),
    image_url: nullable(mediaUrl)(o.image_url),
    translations: translations(o.translations),
    children: array(o.children, categoryNode),
  };
}

// --- Merchant inventory (/api/manage/inventory/) --------------------------------------------------

export interface StockLevel {
  id: string;
  variant: { id: string; sku: string; title: string; product: string; product_name: string };
  location: { id: string; code: string; name: string };
  on_hand: string;
  reserved: string;
  available: string;
  low_stock_threshold: string | null;
  is_low: boolean;
  updated_at: string;
}
export function stockLevel(value: unknown): StockLevel {
  const o = object(value);
  const variant = object(o.variant);
  const location = object(o.location);
  return {
    id: string(o.id),
    variant: {
      id: string(variant.id),
      sku: string(variant.sku),
      title: string(variant.title),
      product: string(variant.product),
      product_name: string(variant.product_name),
    },
    location: { id: string(location.id), code: string(location.code), name: string(location.name) },
    on_hand: decimal(o.on_hand),
    reserved: decimal(o.reserved),
    available: decimal(o.available),
    low_stock_threshold: nullable(decimal)(o.low_stock_threshold),
    is_low: boolean(o.is_low),
    updated_at: string(o.updated_at),
  };
}
export interface StockMovement {
  id: string;
  operation_id: string;
  occurred_at: string;
  movement_type: MovementType;
  variant: string;
  sku: string;
  location: string;
  location_code: string;
  on_hand_delta: string;
  reserved_delta: string;
  on_hand_after: string;
  reserved_after: string;
  batch: string | null;
  serial: string | null;
  reversal_of: string | null;
  reason: string;
}
export function stockMovement(value: unknown): StockMovement {
  const o = object(value);
  return {
    id: string(o.id),
    operation_id: string(o.operation_id),
    occurred_at: string(o.occurred_at),
    movement_type: oneOf(MOVEMENT_TYPES)(o.movement_type),
    variant: string(o.variant),
    sku: string(o.sku),
    location: string(o.location),
    location_code: string(o.location_code),
    on_hand_delta: decimal(o.on_hand_delta),
    reserved_delta: decimal(o.reserved_delta),
    on_hand_after: decimal(o.on_hand_after),
    reserved_after: decimal(o.reserved_after),
    batch: nullable(string)(o.batch),
    serial: nullable(string)(o.serial),
    reversal_of: nullable(string)(o.reversal_of),
    reason: string(o.reason),
  };
}
export interface InventoryOperation {
  operation_id: string;
  replayed: boolean;
  movements: StockMovement[];
}
export function inventoryOperation(value: unknown): InventoryOperation {
  const o = object(value);
  return {
    operation_id: string(o.operation_id),
    replayed: boolean(o.replayed),
    movements: array(o.movements, stockMovement),
  };
}
