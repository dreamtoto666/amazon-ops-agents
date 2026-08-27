import { proxyAgentRequest } from '@/lib/agent-backend';

interface RouteContext {
  params: Promise<{ token: string }>;
}
export async function POST(request: Request, context: RouteContext): Promise<Response> {
  const { token } = await context.params;
  const response = await proxyAgentRequest(
    `/api/auth/password-reset/${encodeURIComponent(token)}/confirm`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: await request.text() }
  );
  return new Response(response.body, { status: response.status });
}
