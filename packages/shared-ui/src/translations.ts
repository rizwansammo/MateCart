/**
 * Which language merchant screens offer translation fields for (Phase 05.1). The store's default
 * language is edited in the main fields; the backend accepts translations only for the store's
 * other supported languages, so this picks the first of those (English or Bangla today).
 */

export const LANGUAGE_NAMES: Readonly<Record<string, string>> = { en: 'English', bn: 'Bangla' };

export interface TranslationLanguage {
  code: string;
  name: string;
}

export function translationLanguage(
  languages: readonly string[],
  defaultLanguage: string,
): TranslationLanguage | null {
  const code = languages.find((language) => language !== defaultLanguage);
  return code ? { code, name: LANGUAGE_NAMES[code] ?? code } : null;
}

export type TranslationMap = Record<string, Record<string, string>>;

/** `existing` with `fields` set for `code`: other languages and other fields are kept. */
export function withTranslation(
  existing: TranslationMap,
  code: string,
  fields: Record<string, string>,
): TranslationMap {
  return { ...existing, [code]: { ...existing[code], ...fields } };
}
