import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ runId: string }>;
}

export async function GET(request: Request, context: RouteContext): Promise<Response> {
  const { runId } = await context.params;
  const lastEventId = request.headers.get('Last-Event-ID');
  const response = await proxyAuthenticatedAgentRequest(
    request,
    `/api/ad-diagnostics/runs/${encodeURIComponent(runId)}/events`,
    {
      headers: lastEventId ? { 'Last-Event-ID': lastEventId } : undefined,
      signal: request.signal
    }
  );
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Cache-Control': 'no-cache, no-transform',
      'Content-Type': response.headers.get('Content-Type') ?? 'text/event-stream; charset=utf-8',
      'X-Accel-Buffering': 'no'
    }
  });
}
