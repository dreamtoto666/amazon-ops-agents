import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ conversationId: string }>;
}

export async function GET(request: Request, context: RouteContext): Promise<Response> {
  const { conversationId } = await context.params;
  const response = await proxyAuthenticatedAgentRequest(
    request,
    `/api/conversations/${encodeURIComponent(conversationId)}/messages`
  );
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json'
    }
  });
}
