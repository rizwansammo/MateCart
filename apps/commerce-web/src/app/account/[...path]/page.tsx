import { notFound } from 'next/navigation';
import { CustomerApp } from '@/components/customer-app';
export default async function AccountPage({ params }: { params: Promise<{ path?: string[] }> }) {
  const path = (await params).path?.join('/') ?? '';
  if (!['', 'sign-in', 'register', 'forgot-password', 'reset-password'].includes(path)) notFound();
  return <CustomerApp path={path} />;
}
