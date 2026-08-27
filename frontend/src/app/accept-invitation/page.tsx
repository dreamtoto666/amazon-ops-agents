import { SetPasswordForm } from '@/features/auth/components/password-pages';
export const metadata = { title: '接受邀请' };
export default function Page() {
  return <SetPasswordForm invitation />;
}
