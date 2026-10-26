import type { CategoryNode } from '@matecart/shared-contracts';
import { StoreThemeScope } from '@matecart/shared-ui';
import type { ReactNode } from 'react';

import type { StoreContext } from '@/lib/store-context';

import { StoreFooter } from './store-footer';
import { StoreHeader } from './store-header';

/**
 * The customer-facing frame of every storefront page. The preset theme is applied on the server
 * (StoreThemeScope), so the first paint already has the store's colours and type; `lang` marks
 * the customer's language for assistive technology and font selection.
 */
export function StoreShell({
  ctx,
  categories,
  children,
}: {
  ctx: StoreContext;
  categories: CategoryNode[];
  children: ReactNode;
}) {
  return (
    <StoreThemeScope theme={ctx.theme}>
      <div lang={ctx.lang} className="flex min-h-dvh flex-col">
        <a
          href="#main"
          className="sr-only z-50 bg-store-action px-4 py-2 text-store-on-action focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          {ctx.t.skipToContent}
        </a>
        <StoreHeader ctx={ctx} categories={categories} />
        <main id="main" tabIndex={-1} className="flex flex-1 flex-col outline-none">
          {children}
        </main>
        <StoreFooter ctx={ctx} categories={categories} />
      </div>
    </StoreThemeScope>
  );
}
