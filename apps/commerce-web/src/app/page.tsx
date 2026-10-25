import { Wordmark } from '@matecart/shared-ui';

import { ReadinessSummary } from '@/components/readiness-summary';
import { getCommerceApiReadiness } from '@/lib/commerce-api';

export const dynamic = 'force-dynamic';

// Phase 01 developer status page. It is replaced by the storefront in Phase 06 and is not a design.
export default async function FoundationPage() {
  const readiness = await getCommerceApiReadiness();

  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-6 px-6 py-16">
      <Wordmark className="text-3xl" />
      <section className="rounded-base border border-line bg-surface p-6 shadow-card">
        <p className="text-xs font-bold tracking-[0.08em] text-brand-deep uppercase">
          Phase 01 · Foundation build
        </p>
        <h1 className="mt-2 text-2xl font-bold tracking-tight">Commerce web</h1>
        <p className="mt-2 text-sm text-muted">
          Verifies the toolchain end to end. The storefront (Phase 06) and merchant dashboard (Phase
          11) will be built from the approved MateCart UI prototype.
        </p>
        <ReadinessSummary label="commerce-api" readiness={readiness} />
      </section>
    </main>
  );
}
