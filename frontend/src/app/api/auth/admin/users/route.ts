import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export async function GET(request: Request): Promise<Response> {
  const response = await proxyAuthenticatedAgentRequest(request, '/api/auth/admin/users');
  return new Response(response.body, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('Content-Type') ?? 'application/json' }
  });
}

export async function POST(request: Request): Promise<Response> {
  const response = await proxyAuthenticatedAgentRequest(request, '/api/auth/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: await request.text()
  });
  return new Response(response.body, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('Content-Type') ?? 'application/json' }
  });
}
