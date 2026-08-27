import { AuthBrand, LoginForm } from '@/features/auth/components/login-form';

export const metadata = { title: '登录' };
export default function LoginPage() {
  return (
    <main className='grid min-h-svh lg:grid-cols-2'>
      <section className='bg-primary hidden p-10 lg:flex lg:items-center lg:justify-center'>
        <AuthBrand />
      </section>
      <section className='flex items-center justify-center bg-background px-5 py-10'>
        <div className='w-full max-w-sm'>
          <div className='mb-8 lg:hidden'>
            <span className='bg-primary text-primary-foreground inline-flex size-9 items-center justify-center rounded-lg'>
              ✦
            </span>
            <p className='mt-3 font-semibold'>Amazon Ops</p>
            <p className='text-muted-foreground text-sm'>多 Agent 运营驾驶舱</p>
          </div>
          <div className='mb-7'>
            <h1 className='text-2xl font-semibold tracking-tight'>欢迎回来</h1>
            <p className='text-muted-foreground mt-2 text-sm'>使用用户名登录运营驾驶舱。</p>
          </div>
          <LoginForm />
        </div>
      </section>
    </main>
  );
}
