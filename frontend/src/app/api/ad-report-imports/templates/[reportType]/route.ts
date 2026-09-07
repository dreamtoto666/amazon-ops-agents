import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';
interface RouteContext { params: Promise<{ reportType: string }> }
export async function GET(request: Request, { params }: RouteContext): Promise<Response> {
  const { reportType } = await params;
  return proxyAuthenticatedAgentRequest(request, `/api/ad-report-imports/templates/${encodeURIComponent(reportType)}`);
}
