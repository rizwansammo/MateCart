import { notFound } from 'next/navigation';
import { ManageApp } from '@/components/manage-app';
import { isManagePath } from '@/lib/manage-routes';
export default async function ManagePage({ params }: { params: Promise<{ path?: string[] }> }) {
  const path = (await params).path?.join('/') ?? '';
  if (!isManagePath(path)) notFound();
  return <ManageApp path={path} />;
}
