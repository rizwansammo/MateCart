'use client';

import type { StaffSession } from '@matecart/shared-contracts';
import { Card, Detail, PageHeader } from './kit';

const PRESET_LABEL: Record<string, string> = {
  fashion: 'Fashion',
  grocery: 'Grocery',
  electronics: 'Electronics',
  general: 'Home & Decor',
  books: 'Bookstore',
};

export function SettingsHub({ session }: { session: StaffSession }) {
  const can = (permission: string) => session.permissions.includes(permission);
  const areas = [
    {
      href: '/manage/appearance',
      title: 'Branding and store features',
      body: 'Store name, logo, colours, announcement, and which optional features are switched on.',
      allowed: can('content.manage'),
    },
    {
      href: '/manage/content',
      title: 'Homepage and pages',
      body: 'Homepage sections, policies and information pages.',
      allowed: can('content.manage'),
    },
    {
      href: '/manage/settings/notifications',
      title: 'Email and notifications',
      body: 'Email provider (SMTP or MateMail), SMS sender, message templates and delivery history.',
      allowed: can('store.settings'),
    },
    {
      href: '/manage/team',
      title: 'Team and access',
      body: 'Invite staff, change roles, suspend or restore access.',
      allowed: can('staff.manage'),
    },
    {
      href: '/manage/account/security',
      title: 'Your account security',
      body: 'Password, two-step verification and recovery codes.',
      allowed: true,
    },
  ].filter((area) => area.allowed);
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Store"
        title="Settings"
        intro="Everything about how your store looks and who can work in it."
      />
      <div className="grid gap-4 md:grid-cols-2">
        {areas.map((area) => (
          <a
            key={area.href}
            href={area.href}
            className="rounded-base border border-line bg-surface p-5 hover:border-ink"
          >
            <span className="text-base font-bold">{area.title} →</span>
            <p className="mt-2 text-sm leading-6 text-muted">{area.body}</p>
          </a>
        ))}
      </div>
      <Card title="Store details">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Detail label="Store">{session.store_name}</Detail>
          <Detail label="Business type">
            {PRESET_LABEL[session.store_preset] ?? (session.store_preset || '—')}
          </Detail>
          <Detail label="Currency">{session.store_currency || '—'}</Detail>
          <Detail label="Time zone">{session.store_timezone || '—'}</Detail>
        </dl>
        <p className="mt-4 text-xs leading-5 text-muted">
          Your business type, theme, domain, payment and courier connections are set up by NetaMate.
          Contact NetaMate support to change them.
        </p>
      </Card>
    </div>
  );
}
