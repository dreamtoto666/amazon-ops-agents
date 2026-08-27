'use client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { FormEvent, useState } from 'react';

export function ForgotPasswordForm() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    const response = await fetch('/api/auth/password-reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    });
    const data = await response.json();
    setMessage(response.ok ? data.message : data.detail);
  }
  return (
    <AuthPanel title='找回密码' description='输入工作邮箱，我们会发送密码重置说明。'>
      <form className='space-y-4' onSubmit={submit}>
        <Input
          type='email'
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder='name@company.com'
          required
          className='h-11'
        />
        {message && <p className='text-sm'>{message}</p>}
        <Button className='h-11 w-full'>发送重置说明</Button>
      </form>
    </AuthPanel>
  );
}
export function SetPasswordForm({ invitation = false }: { invitation?: boolean }) {
  const params = useSearchParams();
  const token = params.get('token');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!token) return setMessage('链接无效或已过期。');
    const path = invitation
      ? `/api/auth/invitations/${token}/accept`
      : `/api/auth/password-reset/${token}/confirm`;
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });
    if (response.ok) setMessage(invitation ? '密码设置完成，请登录。' : '密码已重置，请登录。');
    else {
      const data = await response.json();
      setMessage(data.detail ?? '操作失败。');
    }
  }
  return (
    <AuthPanel
      title={invitation ? '设置账号密码' : '设置新密码'}
      description='密码至少需要 6 个字符。'
    >
      <form className='space-y-4' onSubmit={submit}>
        <Input
          type='password'
          autoComplete='new-password'
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={6}
          className='h-11'
        />
        {message && <p className='text-sm'>{message}</p>}
        <Button className='h-11 w-full'>{invitation ? '完成设置' : '重置密码'}</Button>
      </form>
    </AuthPanel>
  );
}
function AuthPanel({
  title,
  description,
  children
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <main className='flex min-h-svh items-center justify-center p-5'>
      <section className='w-full max-w-sm'>
        <Link href='/login' className='font-semibold'>
          Amazon Ops
        </Link>
        <h1 className='mt-10 text-2xl font-semibold'>{title}</h1>
        <p className='text-muted-foreground mt-2 mb-6 text-sm'>{description}</p>
        {children}
      </section>
    </main>
  );
}
