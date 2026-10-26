import type { PublicProductDetail, PublicVariant } from '@matecart/shared-contracts';

/**
 * Variant choice on the product page. One variant is always selected, so price, stock and the
 * ISBN always describe a real variant. A variant is purchasable only when it has a price and the
 * API reports it in stock (the API counts pre-orders and digital variants as in stock).
 */

export function isPurchasable(variant: PublicVariant): boolean {
  return variant.in_stock && variant.price !== null;
}

function optionsOf(variant: PublicVariant): Record<string, string> {
  return Object.fromEntries(variant.options.map((o) => [o.attribute, o.option]));
}

/** The requested variant when it exists, else the first purchasable one, else the first. */
export function initialVariant(
  product: PublicProductDetail,
  requested?: string | null,
): PublicVariant | null {
  return (
    product.variants.find((v) => v.id === requested) ??
    product.variants.find(isPurchasable) ??
    product.variants[0] ??
    null
  );
}

/**
 * The variant to show after choosing `option` for `attribute`: the same other choices when
 * that variant can be bought, else another purchasable variant with the option, else the
 * matching variant even if it cannot be bought (so its state is shown honestly).
 */
export function selectOption(
  product: PublicProductDetail,
  current: PublicVariant,
  attribute: string,
  option: string,
): PublicVariant {
  const wanted = { ...optionsOf(current), [attribute]: option };
  const withOption = product.variants.filter((v) => optionsOf(v)[attribute] === option);
  const exact = withOption.filter((v) =>
    Object.entries(wanted).every(([code, value]) => optionsOf(v)[code] === value),
  );
  return (
    exact.find(isPurchasable) ??
    withOption.find(isPurchasable) ??
    exact[0] ??
    withOption[0] ??
    current
  );
}

export type OptionState = 'selected' | 'available' | 'unavailable';

/** How to show `option`: unavailable only when no purchasable variant has it at all. */
export function optionState(
  product: PublicProductDetail,
  current: PublicVariant,
  attribute: string,
  option: string,
): OptionState {
  if (optionsOf(current)[attribute] === option) return 'selected';
  const buyable = product.variants.some(
    (v) => isPurchasable(v) && optionsOf(v)[attribute] === option,
  );
  return buyable ? 'available' : 'unavailable';
}

/** True when the variant releases after `today` (YYYY-MM-DD): a pre-order, not stock on hand. */
export function isPreorder(variant: PublicVariant, today: string): boolean {
  return variant.release_date !== null && variant.release_date > today;
}

/** "500.000" g → "500 g"; "1.500" kg → "1.5 kg"; null when the variant has no net contents. */
export function packSize(variant: PublicVariant): string | null {
  if (!variant.net_quantity || !variant.net_unit) return null;
  const quantity = variant.net_quantity.includes('.')
    ? variant.net_quantity.replace(/\.?0+$/, '')
    : variant.net_quantity;
  return `${quantity} ${variant.net_unit}`;
}
