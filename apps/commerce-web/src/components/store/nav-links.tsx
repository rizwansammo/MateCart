'use client';

import type { Route } from 'next';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { FOCUS } from './ui';

export interface NavItem {
  label: string;
  href: Route;
}

/** Links that mark the page the customer is on (aria-current), styled by the caller's preset. */
export function NavLinks({
  items,
  label,
  className,
  linkClassName,
  activeClassName,
}: {
  items: NavItem[];
  label: string;
  className: string;
  linkClassName: string;
  activeClassName: string;
}) {
  const pathname = usePathname();
  return (
    <nav aria-label={label}>
      <ul className={`m-0 list-none p-0 ${className}`}>
        {items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.href} className="flex-none">
              <Link
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`${linkClassName} ${active ? activeClassName : ''} ${FOCUS}`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
