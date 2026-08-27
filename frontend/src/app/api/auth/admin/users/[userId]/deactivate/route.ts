import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';
interface RouteContext {
  params: Promise<{ userId: string }>;
}
export async function POST(request: Request, context: RouteContext): Promise<Response> {
  const { userId } = await context.params;
  const response = await proxyAuthenticatedAgentRequest(
    request,
    `/api/auth/admin/users/${encodeURIComponent(userId)}/deactivate`,
    { method: 'POST' }
  );
  return new Response(response.body, { status: response.status });
}
