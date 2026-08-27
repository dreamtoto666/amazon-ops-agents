import { proxyAuthenticatedAgentRequest, SESSION_COOKIE } from '@/lib/agent-backend';
import { NextResponse } from 'next/server';

export async function POST(request: Request): Promise<Response> {
  await proxyAuthenticatedAgentRequest(request, '/api/auth/logout', { method: 'POST' });
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, '', {
    httpOnly: true,
    sameSite: 'lax',
    path: '/',
    maxAge: 0
  });
  return response;
}
