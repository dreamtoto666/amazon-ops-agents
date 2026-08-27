import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ runId: string }>;
}

export async function DELETE(request: Request, context: RouteContext): Promise<Response> {
  const { runId } = await context.params;
  const response = await proxyAuthenticatedAgentRequest(
    request,
    `/api/ad-diagnostics/history/${encodeURIComponent(runId)}`,
    { method: 'DELETE' }
  );
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json'
    }
  });
}
