import { jsonLd } from '@/lib/seo';

/** Structured data as a script element; `jsonLd` escapes `<`, so no value can end the script. */
export function JsonLd({ data }: { data: Record<string, unknown> | null }) {
  if (!data) return null;
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(data) }} />;
}
