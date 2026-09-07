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
  // The deployment may be temporarily served over plain HTTP (for example
  // directly via an IP before a TLS domain is configured). Only mark the
  // session cookie as Secure when the request actually arrived over HTTPS;
  // otherwise browsers silently discard it and login appears to do nothing.
  const forwardedProto = request.headers.get('x-forwarded-proto');
  next.cookies.set(SESSION_COOKIE, payload.session_token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: forwardedProto === 'https' || request.url.startsWith('https://'),
    path: '/',
    expires: new Date(payload.expires_at)
  });
  return next;
}
