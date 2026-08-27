'use client';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { IconEye, IconEyeOff, IconLock, IconSparkles } from '@tabler/icons-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { FormEvent, useState } from 'react';

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const next = searchParams.get('next');
  const destination = next?.startsWith('/dashboard') ? next : '/dashboard/chat';

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    if (!username.trim()) return setError('请输入用户名。');
    if (!password) return setError('请输入密码。');
    setLoading(true);
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, remember })
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(payload.detail ?? '登录失败，请稍后重试。');
        return;
      }
      router.replace(destination);
      router.refresh();
    } catch {
      setError('无法连接到服务，请检查网络后重试。');
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className='space-y-5' noValidate>
      <div className='space-y-2'>
        <label htmlFor='username' className='text-sm font-medium'>
          用户名
        </label>
        <div className='relative'>
          <Input
            id='username'
            autoComplete='username'
            type='text'
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder='输入用户名'
            className='h-11'
            disabled={loading}
          />
        </div>
      </div>
      <div className='space-y-2'>
        <div className='flex items-center justify-between'>
          <label htmlFor='password' className='text-sm font-medium'>
            密码
          </label>
        </div>
        <div className='relative'>
          <IconLock className='text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2' />
          <Input
            id='password'
            autoComplete='current-password'
            type={showPassword ? 'text' : 'password'}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className='h-11 px-9'
            disabled={loading}
          />
          <button
            type='button'
            onClick={() => setShowPassword(!showPassword)}
            className='text-muted-foreground hover:text-foreground absolute top-1/2 right-3 -translate-y-1/2'
            aria-label={showPassword ? '隐藏密码' : '显示密码'}
          >
            {showPassword ? <IconEyeOff className='size-4' /> : <IconEye className='size-4' />}
          </button>
        </div>
      </div>
      <div className='flex items-center gap-2 text-sm'>
        <Checkbox
          id='remember'
          checked={remember}
          onCheckedChange={(value) => setRemember(value === true)}
        />
        <label htmlFor='remember' className='cursor-pointer'>
          30 天内保持登录
        </label>
      </div>
      {error && (
        <p role='alert' className='rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive'>
          {error}
        </p>
      )}
      <Button type='submit' size='lg' className='h-11 w-full' disabled={loading}>
        {loading ? '正在登录…' : '登录驾驶舱'}
      </Button>
      <p className='text-muted-foreground text-center text-xs'>账号由管理员直接创建；忘记密码请联系管理员。</p>
    </form>
  );
}

export function AuthBrand() {
  return (
    <div className='space-y-8 text-primary-foreground'>
      <div className='flex items-center gap-3'>
        <span className='flex size-10 items-center justify-center rounded-xl bg-primary-foreground text-primary'>
          <IconSparkles className='size-5' />
        </span>
        <div>
          <p className='font-semibold'>Amazon Ops</p>
          <p className='text-primary-foreground/60 text-sm'>多 Agent 运营驾驶舱</p>
        </div>
      </div>
      <div className='space-y-3'>
        <h1 className='text-3xl font-semibold tracking-tight'>
          让运营决策
          <br />
          更有依据。
        </h1>
        <p className='max-w-sm text-sm leading-6 text-primary-foreground/70'>
          连接业务数据与专业 Agent，将分析、诊断和待办统一沉淀在一个工作台。
        </p>
      </div>
      <div className='space-y-3 border-t border-primary-foreground/20 pt-6 text-sm'>
        <div>
          <p className='font-medium'>AI 运营助手</p>
          <p className='mt-1 text-primary-foreground/60'>结构化问题理解与可追溯的事实结论。</p>
        </div>
        <div>
          <p className='font-medium'>广告异常诊断</p>
          <p className='mt-1 text-primary-foreground/60'>巡检、归因、策略与复核待办闭环。</p>
        </div>
      </div>
    </div>
  );
}
