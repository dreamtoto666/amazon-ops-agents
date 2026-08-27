import { proxyAuthenticatedAgentRequest } from '@/lib/agent-backend';

export const dynamic = 'force-dynamic';

export async function POST(request: Request): Promise<Response> {
  const idempotencyKey = request.headers.get('Idempotency-Key');
  const response = await proxyAuthenticatedAgentRequest(request, '/api/ad-diagnostics/runs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {})
    },
    body: await request.text()
  });
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
      ...(response.headers.get('Idempotency-Key')
        ? { 'Idempotency-Key': response.headers.get('Idempotency-Key')! }
        : {}),
      ...(response.headers.get('Idempotency-Replayed')
        ? { 'Idempotency-Replayed': response.headers.get('Idempotency-Replayed')! }
        : {})
    }
  });
}
