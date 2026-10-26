import type { PriceRange, PublicPrice } from '@matecart/shared-contracts';

/**
 * Money display for the storefront. Amounts arrive as decimal strings ("1250.0000") and are
 * formatted as text, never through floating point: the currency's symbol and minor unit come
 * from Intl, the digits from the string. Whole amounts drop their decimals ("৳1,250").
 */

const SCALE = 4; // the API's decimal places
const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

function currencyParts(currency: string): { symbol: string; digits: number } {
  try {
    const format = new Intl.NumberFormat('en', {
      style: 'currency',
      currency,
      currencyDisplay: 'narrowSymbol',
    });
    const symbol = format.formatToParts(0).find((part) => part.type === 'currency')?.value;
    return {
      symbol: symbol ?? currency,
      digits: format.resolvedOptions().maximumFractionDigits ?? 2,
    };
  } catch {
    return { symbol: currency, digits: 2 };
  }
}

/** "1250.5000" in BDT → "৳1,250.50"; "1250.0000" → "৳1,250". */
export function formatMoney(amount: string, currency: string): string {
  const match = DECIMAL.exec(amount.trim());
  if (!match) return amount;
  const [, sign = '', whole = '0', fraction = ''] = match;
  const { symbol, digits } = currencyParts(currency);
  const grouped = whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const minor = fraction.slice(0, digits).padEnd(digits, '0');
  const shown = /^0*$/.test(minor) ? '' : `.${minor}`;
  return `${sign}${symbol}${grouped}${shown}`;
}

/** Exact decimal in ten-thousandths, for comparisons and differences without rounding. */
export function toUnits(amount: string): bigint | null {
  const match = DECIMAL.exec(amount.trim());
  if (!match) return null;
  const [, sign, whole = '0', fraction = ''] = match;
  const units = BigInt(whole + fraction.slice(0, SCALE).padEnd(SCALE, '0'));
  return sign ? -units : units;
}

export function fromUnits(units: bigint): string {
  const negative = units < 0n;
  const text = (negative ? -units : units).toString().padStart(SCALE + 1, '0');
  return `${negative ? '-' : ''}${text.slice(0, -SCALE)}.${text.slice(-SCALE)}`;
}

/** The saving shown next to a compare-at price, or null when there is none. */
export function saving(price: PublicPrice): string | null {
  if (!price.compare_at_amount) return null;
  const now = toUnits(price.amount);
  const before = toUnits(price.compare_at_amount);
  if (now === null || before === null || before <= now) return null;
  return fromUnits(before - now);
}

/** A product card's price: one amount, or the lowest with "From" when variants differ. */
export function priceLabel(
  range: PriceRange,
  from: (price: string) => string,
): { text: string; isRange: boolean } {
  const low = formatMoney(range.min_amount, range.currency);
  const same = toUnits(range.min_amount) === toUnits(range.max_amount);
  return same ? { text: low, isRange: false } : { text: from(low), isRange: true };
}
