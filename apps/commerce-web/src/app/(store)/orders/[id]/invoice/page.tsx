import type { Metadata } from 'next';

import { InvoiceApp } from '@/components/store/invoice-app';
import { CONTAINER } from '@/components/store/ui';
import { getStoreContext } from '@/lib/store-context';

export const metadata: Metadata = { title: 'Invoice', robots: { index: false, follow: false } };

export default async function InvoicePage({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, { lang }] = await Promise.all([params, getStoreContext()]);
  return (
    <div className={`${CONTAINER} py-8 sm:py-12 print:py-0`}>
      <InvoiceApp id={id} lang={lang} />
    </div>
  );
}
