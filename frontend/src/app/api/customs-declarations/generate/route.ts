import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

export async function POST(request: Request): Promise<Response> {
  return proxyAuthenticatedAgentRequest(request, '/api/customs-declarations/generate', {
    method: 'POST',
    headers: { 'Content-Type': request.headers.get('Content-Type') ?? '' },
    body: await request.arrayBuffer()
  });
}
