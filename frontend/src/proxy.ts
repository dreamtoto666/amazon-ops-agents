import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function proxy(_req: NextRequest) {
  const { nextUrl, cookies } = _req;
  const hasSession = Boolean(cookies.get('amazon_ops_session')?.value);
  if (
    nextUrl.pathname.startsWith('/api/') &&
    !nextUrl.pathname.startsWith('/api/auth/') &&
    nextUrl.pathname !== '/api/agent/health' &&
    !hasSession
  ) {
    return NextResponse.json({ detail: '请先登录' }, { status: 401 });
  }
  if (nextUrl.pathname.startsWith('/dashboard') && !hasSession) {
    const loginUrl = new URL('/login', _req.url);
    loginUrl.searchParams.set('next', `${nextUrl.pathname}${nextUrl.search}`);
    return NextResponse.redirect(loginUrl);
  }
  if (nextUrl.pathname === '/login' && hasSession) {
    return NextResponse.redirect(new URL('/dashboard/chat', _req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    '/(api|trpc)(.*)'
  ]
};
