import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const response = await proxyAuthenticatedAgentRequest(request, '/api/conversations');
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json'
    }
  });
}
