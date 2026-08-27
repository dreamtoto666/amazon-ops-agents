import { AGENT_API_BASE_URL, SESSION_COOKIE } from '@/lib/agent-backend';
import { NextResponse } from 'next/server';

export async function POST(request: Request): Promise<Response> {
  const response = await fetch(`${AGENT_API_BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: await request.text(),
    cache: 'no-store'
  });
  const payload = await response.json();
  if (!response.ok) return NextResponse.json(payload, { status: response.status });
  const next = NextResponse.json({ user: payload.user });
  next.cookies.set(SESSION_COOKIE, payload.session_token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    expires: new Date(payload.expires_at)
  });
  return next;
}
