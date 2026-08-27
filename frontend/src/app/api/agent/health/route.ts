import { proxyAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  const response = await proxyAgentRequest('/api/health');
  return new Response(response.body, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('Content-Type') ?? 'application/json' }
  });
}
