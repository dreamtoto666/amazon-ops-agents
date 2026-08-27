export const AGENT_API_BASE_URL = (
  process.env.AGENT_API_BASE_URL ?? 'http://127.0.0.1:8000'
).replace(/\/$/, '');

export const SESSION_COOKIE = 'amazon_ops_session';

export function sessionTokenFromRequest(request: Request): string | undefined {
  const cookie = request.headers.get('cookie') ?? '';
  return cookie
    .split(';')
    .map((item) => item.trim().split('='))
    .find(([name]) => name === SESSION_COOKIE)?.[1];
}

export async function proxyAgentRequest(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${AGENT_API_BASE_URL}${path}`, {
      cache: 'no-store',
      ...init
    });
  } catch {
    return Response.json(
      {
        detail: 'Amazon Ops Agent 后端未启动或无法连接。'
      },
      { status: 503 }
    );
  }
}

export async function proxyAuthenticatedAgentRequest(
  request: Request,
  path: string,
  init?: RequestInit
): Promise<Response> {
  const token = sessionTokenFromRequest(request);
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return proxyAgentRequest(path, {
    ...init,
    headers
  });
}
