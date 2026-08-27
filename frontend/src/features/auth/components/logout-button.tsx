'use client';
import { Button } from '@/components/ui/button';
import { Icons } from '@/components/icons';
import { useRouter } from 'next/navigation';

export function LogoutButton() {
  const router = useRouter();
  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    router.replace('/login');
    router.refresh();
  }
  return (
    <Button variant='ghost' size='icon' onClick={logout} aria-label='退出登录'>
      <Icons.logout className='size-4' />
    </Button>
  );
}
