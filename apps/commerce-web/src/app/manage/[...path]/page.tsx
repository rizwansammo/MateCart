import { notFound } from 'next/navigation';
import { ManageApp } from '@/components/manage-app';
export default async function ManagePage({ params }: { params: Promise<{ path?: string[] }> }) {
  const path = (await params).path?.join('/') ?? '';
  if (
    ![
      '',
      'sign-in',
      'sign-in/verify',
      'forgot-password',
      'reset-password',
      'invitation',
      'team',
      'account/security',
    ].includes(path)
  )
    notFound();
  return <ManageApp path={path} />;
}
