import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  return proxyAuthenticatedAgentRequest(request, '/api/team-knowledge/vault');
}

export async function POST(request: Request): Promise<Response> {
  return proxyAuthenticatedAgentRequest(request, '/api/team-knowledge/vault', {
    method: 'POST',
    headers: { 'Content-Type': request.headers.get('Content-Type') ?? '' },
    body: await request.arrayBuffer()
  });
}
