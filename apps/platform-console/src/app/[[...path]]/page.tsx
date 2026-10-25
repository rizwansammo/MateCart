import { notFound } from 'next/navigation';
import { ConsoleApp } from '@/components/console-app';
export default async function ConsolePage({ params }: { params: Promise<{ path?: string[] }> }) {
  const path = (await params).path?.join('/') ?? '';
  if (
    ![
      '',
      'sign-in',
      'sign-in/verify',
      'activate',
      'enroll',
      'forgot-password',
      'reset-password',
      'account/security',
    ].includes(path)
  )
    notFound();
  return <ConsoleApp path={path} />;
}
