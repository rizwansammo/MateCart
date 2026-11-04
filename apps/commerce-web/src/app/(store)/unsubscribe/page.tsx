import type { Metadata } from 'next';

import { UnsubscribeForm } from '@/components/customer-notifications';
import { CONTAINER } from '@/components/store/ui';
import { getStoreContext } from '@/lib/store-context';

// A personal, single-purpose page: never indexed, never cached.
export const metadata: Metadata = { robots: { index: false, follow: false } };

interface Props {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function UnsubscribePage({ searchParams }: Props) {
  const [params, { t }] = await Promise.all([searchParams, getStoreContext()]);
  const raw = params.token;
  const token = typeof raw === 'string' && raw.length <= 600 ? raw : '';
  return (
    <div className={`${CONTAINER} max-w-xl py-12`}>
      <h1 className="mb-4 text-2xl font-bold">{t.unsubscribeTitle}</h1>
      <UnsubscribeForm
        token={token}
        // Plain strings only: message objects hold functions a client component cannot receive.
        t={{
          unsubscribeMissing: t.unsubscribeMissing,
          unsubscribeDone: t.unsubscribeDone,
          unsubscribeBody: t.unsubscribeBody,
          unsubscribeFailed: t.unsubscribeFailed,
          unsubscribeButton: t.unsubscribeButton,
        }}
      />
    </div>
  );
}
