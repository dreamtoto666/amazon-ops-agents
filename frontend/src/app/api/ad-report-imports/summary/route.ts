import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  return proxyAuthenticatedAgentRequest(request, '/api/ad-report-imports/summary');
}
