import type { Metadata } from 'next';

import { OrderApp } from '@/components/store/order-app';
import { CONTAINER } from '@/components/store/ui';
import { getStoreContext } from '@/lib/store-context';

export const metadata: Metadata = { title: 'Order', robots: { index: false, follow: false } };

export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, { lang }] = await Promise.all([params, getStoreContext()]);
  return (
    <div className={`${CONTAINER} py-8 sm:py-12`}>
      <OrderApp id={id} lang={lang} />
    </div>
  );
}
