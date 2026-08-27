'use client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FormEvent, useEffect, useState } from 'react';

interface User {
  id: string;
  username: string;
  role: 'admin' | 'operator';
  active: boolean;
}
export function AccountManagement() {
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'admin' | 'operator'>('operator');
  const [message, setMessage] = useState('');
  const load = () =>
    fetch('/api/auth/admin/users')
      .then((r) => (r.ok ? r.json() : []))
      .then(setUsers);
  useEffect(() => {
    void load();
  }, []);
  async function createDirectly(event: FormEvent) {
    event.preventDefault();
    setMessage('');
    const response = await fetch('/api/auth/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role })
    });
    const payload = await response.json();
    setMessage(response.ok ? '账号已创建，可以直接登录。' : (payload.detail ?? '创建失败'));
    if (response.ok) {
      setUsername('');
      setPassword('');
      void load();
    }
  }
  async function action(user: User, name: 'deactivate') {
    const response = await fetch(`/api/auth/admin/users/${user.id}/${name}`, { method: 'POST' });
    setMessage(
      response.ok ? (name === 'deactivate' ? '账号已停用。' : '重置邮件已发送。') : '操作失败。'
    );
    load();
  }
  return (
    <div className='space-y-6 p-4 md:p-6'>
      <div>
        <h1 className='text-2xl font-semibold'>账号管理</h1>
        <p className='text-muted-foreground mt-1 text-sm'>创建用户名账号，并管理现有账号状态。</p>
      </div>
      <form
        onSubmit={createDirectly}
        className='grid gap-3 rounded-xl border p-4 md:grid-cols-[1fr_1fr_auto_auto]'
      >
        <Input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder='用户名（3–32 位）'
          required
          minLength={3}
          maxLength={32}
          pattern='[A-Za-z0-9._-]+'
          autoComplete='username'
          className='h-10'
        />
        <Input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type='password'
          placeholder='初始密码（至少 6 位）'
          required
          minLength={6}
          className='h-10'
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as User['role'])}
          className='h-10 rounded-lg border bg-background px-3 text-sm'
        >
          <option value='operator'>运营者</option>
          <option value='admin'>管理员</option>
        </select>
        <Button type='submit' className='h-10'>
          直接创建
        </Button>
      </form>
      {message && <p className='text-sm'>{message}</p>}
      <div className='overflow-hidden rounded-xl border'>
        <table className='w-full text-sm'>
          <thead className='bg-muted/50 text-left'>
            <tr>
              <th className='p-3 font-medium'>用户名</th>
              <th className='p-3 font-medium'>角色</th>
              <th className='p-3 font-medium'>状态</th>
              <th className='p-3 font-medium'>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className='border-t'>
                <td className='p-3'>{user.username}</td>
                <td className='p-3'>{user.role === 'admin' ? '管理员' : '运营者'}</td>
                <td className='p-3'>{user.active ? '正常' : '已停用'}</td>
                <td className='flex gap-2 p-3'>
                  {user.active && (
                    <>
                      <Button
                        size='sm'
                        variant='destructive'
                        onClick={() => action(user, 'deactivate')}
                      >
                        停用
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
