/**
 * Merchant workspace routes (/manage). Shared by the server page, which 404s unknown paths,
 * and the client app, which shows only permitted navigation. Hiding a link is not
 * authorization: every API still enforces the same permission.
 */
import type { NavGroup } from '@matecart/shared-ui';

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';

/** Merchant sections, each with the permission the API requires for it. */
const SECTIONS: readonly { pattern: RegExp; permission: string | null }[] = [
  { pattern: /^$/, permission: null },
  { pattern: /^orders$/, permission: 'orders.manage' },
  { pattern: /^orders\/new$/, permission: 'orders.manage' },
  { pattern: new RegExp(`^orders/${UUID}(/invoice)?$`), permission: 'orders.manage' },
  { pattern: /^shipments$/, permission: 'couriers.manage' },
  { pattern: new RegExp(`^shipments/${UUID}$`), permission: 'couriers.manage' },
  { pattern: /^products(\/new)?$/, permission: 'catalog.manage' },
  { pattern: new RegExp(`^products/${UUID}$`), permission: 'catalog.manage' },
  { pattern: /^catalog$/, permission: 'catalog.manage' },
  { pattern: /^inventory$/, permission: 'catalog.manage' },
  { pattern: /^customers$/, permission: 'customers.view' },
  { pattern: new RegExp(`^customers/${UUID}$`), permission: 'customers.view' },
  { pattern: /^aftersales$/, permission: 'orders.manage' },
  { pattern: /^reports$/, permission: 'reports.view' },
  { pattern: /^settings$/, permission: null },
  { pattern: /^account\/security$/, permission: null },
  { pattern: /^team$/, permission: 'staff.manage' },
  { pattern: /^(appearance|content)$/, permission: 'content.manage' },
];

const PUBLIC_PATHS = [
  'sign-in',
  'sign-in/verify',
  'forgot-password',
  'reset-password',
  'invitation',
];

/** The permission a merchant section needs, or null when every staff member may open it. */
export function requiredPermission(path: string): string | null {
  return SECTIONS.find((section) => section.pattern.test(path))?.permission ?? null;
}

export function isManagePath(path: string): boolean {
  return PUBLIC_PATHS.includes(path) || SECTIONS.some((section) => section.pattern.test(path));
}

/** Only links the signed-in role may open; the API still refuses everything else. */
export function navigation(permissions: readonly string[]): NavGroup[] {
  const can = (permission: string) => permissions.includes(permission);
  const groups: NavGroup[] = [
    { links: [{ href: '/manage', label: 'Overview' }] },
    {
      heading: 'Sell',
      links: [
        ...(can('orders.manage') ? [{ href: '/manage/orders', label: 'Orders' }] : []),
        ...(can('catalog.manage')
          ? [
              { href: '/manage/products', label: 'Products' },
              { href: '/manage/catalog', label: 'Catalogue' },
              { href: '/manage/inventory', label: 'Inventory' },
            ]
          : []),
        ...(can('customers.view') ? [{ href: '/manage/customers', label: 'Customers' }] : []),
      ],
    },
    {
      heading: 'Operate',
      links: [
        ...(can('couriers.manage') ? [{ href: '/manage/shipments', label: 'Shipments' }] : []),
        ...(can('orders.manage')
          ? [{ href: '/manage/aftersales', label: 'Customer requests' }]
          : []),
        ...(can('reports.view') ? [{ href: '/manage/reports', label: 'Reports' }] : []),
      ],
    },
    {
      heading: 'Store',
      links: [
        ...(can('content.manage')
          ? [
              { href: '/manage/appearance', label: 'Appearance' },
              { href: '/manage/content', label: 'Content' },
            ]
          : []),
        ...(can('staff.manage') ? [{ href: '/manage/team', label: 'Team & access' }] : []),
        { href: '/manage/settings', label: 'Settings' },
        { href: '/manage/account/security', label: 'Account security' },
      ],
    },
  ];
  return groups.filter((group) => group.links.length);
}
