import type { Metadata } from 'next';

import { CartApp } from '@/components/store/cart-app';
import { CONTAINER } from '@/components/store/ui';
import { getStoreContext } from '@/lib/store-context';

export const metadata: Metadata = { title: 'Cart', robots: { index: false, follow: false } };

export default async function CartPage() {
  const { lang } = await getStoreContext();
  return (
    <div className={`${CONTAINER} py-8 sm:py-12`}>
      <CartApp lang={lang} />
    </div>
  );
}
