import { Wordmark } from '@matecart/shared-ui';

import { ReadinessSummary } from '@/components/readiness-summary';
import { getControlApiReadiness } from '@/lib/control-api';

export const dynamic = 'force-dynamic';

// Phase 01 developer status page. The console itself is built in Phase 16.
export default async function FoundationPage() {
  const readiness = await getControlApiReadiness();

  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-6 px-6 py-16">
      <Wordmark className="text-3xl" />
      <section className="rounded-base border border-line bg-surface p-6 shadow-card">
        <p className="text-xs font-bold tracking-[0.08em] text-brand-deep uppercase">
          Phase 01 · Foundation build
        </p>
        <h1 className="mt-2 text-2xl font-bold tracking-tight">NetaMate Platform Console</h1>
        <p className="mt-2 text-sm text-muted">
          Internal operations console. Client registry, deployments and monitoring arrive in Phase
          16. Client stores never depend on this console being available.
        </p>
        <ReadinessSummary label="control-api" readiness={readiness} />
      </section>
    </main>
  );
}
