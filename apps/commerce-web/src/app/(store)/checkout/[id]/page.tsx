import type { Metadata } from 'next';

import { CheckoutApp } from '@/components/store/checkout-app';
import { CONTAINER } from '@/components/store/ui';
import { getStoreContext } from '@/lib/store-context';

export const metadata: Metadata = { title: 'Checkout', robots: { index: false, follow: false } };

export default async function CheckoutPage({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, { lang }] = await Promise.all([params, getStoreContext()]);
  return (
    <div className={`${CONTAINER} py-8 sm:py-12`}>
      <CheckoutApp id={id} lang={lang} />
    </div>
  );
}
