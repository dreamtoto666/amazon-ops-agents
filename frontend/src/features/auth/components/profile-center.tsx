'use client';

import { LogoutButton } from '@/features/auth/components/logout-button';
import { useEffect, useState } from 'react';

interface CurrentUser {
  username: string;
  role: 'admin' | 'operator';
}

export function ProfileCenter() {
  const [user, setUser] = useState<CurrentUser>();

  useEffect(() => {
    fetch('/api/auth/me')
      .then((response) => (response.ok ? response.json() : null))
      .then((value) => setUser(value ?? undefined));
  }, []);

  return (
    <div className='mx-auto w-full max-w-2xl space-y-6 p-4 md:p-6'>
      <div>
        <h1 className='text-2xl font-semibold'>个人中心</h1>
        <p className='text-muted-foreground mt-1 text-sm'>查看当前登录账号与访问权限。</p>
      </div>
      <section className='rounded-xl border p-5'>
        <dl className='space-y-5 text-sm'>
          <div>
            <dt className='text-muted-foreground'>用户名</dt>
            <dd className='mt-1 font-medium'>{user?.username ?? '正在加载…'}</dd>
          </div>
          <div>
            <dt className='text-muted-foreground'>账号角色</dt>
            <dd className='mt-1 font-medium'>{user?.role === 'admin' ? '管理员' : '运营者'}</dd>
          </div>
          <div>
            <dt className='text-muted-foreground'>会话</dt>
            <dd className='mt-1'>默认 12 小时有效；选择保持登录时为 30 天。</dd>
          </div>
        </dl>
      </section>
      <section className='flex items-center justify-between rounded-xl border p-5'>
        <div>
          <h2 className='font-medium'>退出当前账号</h2>
          <p className='text-muted-foreground mt-1 text-sm'>将在这台设备上结束当前会话。</p>
        </div>
        <LogoutButton />
      </section>
    </div>
  );
}
