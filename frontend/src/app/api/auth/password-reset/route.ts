import { proxyAgentRequest } from '@/lib/agent-backend';

export async function POST(request: Request): Promise<Response> {
  const response = await proxyAgentRequest('/api/auth/password-reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: await request.text()
  });
  return new Response(response.body, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('Content-Type') ?? 'application/json' }
  });
}
