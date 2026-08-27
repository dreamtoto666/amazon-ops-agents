'use client';

import { stageEventSchema } from './schema';
import { STAGE_EVENT_NAMES, type StageEvent } from './types';

export interface RunEventHandlers {
  onOpen?: () => void;
  onEvent: (event: StageEvent) => void;
  onHeartbeat?: () => void;
  onError: (error: Error) => void;
}

export interface RunEventSubscription {
  close: () => void;
}

export interface CreateRunResult {
  run_id: string;
  conversation_id: string;
  status: string;
}

export async function createAgentRun(
  message: string,
  conversationId: string,
  idempotencyKey = crypto.randomUUID()
): Promise<CreateRunResult> {
  const response = await fetch('/api/agent/runs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey
    },
    body: JSON.stringify({ message, conversation_id: conversationId })
  });
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: string;
    run_id?: string;
    conversation_id?: string;
    status?: string;
  };
  if (!response.ok || !payload.run_id || !payload.conversation_id) {
    throw new Error(payload.detail ?? `Agent 请求失败（${response.status}）`);
  }
  return {
    run_id: payload.run_id,
    conversation_id: payload.conversation_id,
    status: payload.status ?? 'accepted'
  };
}

export function subscribeToRunEvents(
  runId: string,
  handlers: RunEventHandlers
): RunEventSubscription {
  const endpoint = `/api/agent/runs/${encodeURIComponent(runId)}/events`;
  const source = new EventSource(endpoint);

  source.addEventListener('open', () => handlers.onOpen?.());

  for (const eventName of STAGE_EVENT_NAMES) {
    source.addEventListener(eventName, (message) => {
      const parsedJson: unknown = JSON.parse((message as MessageEvent<string>).data);
      const parsed = stageEventSchema.safeParse(parsedJson);
      if (!parsed.success) {
        handlers.onError(new Error(`收到不符合协议的 SSE 事件：${eventName}`));
        return;
      }
      handlers.onEvent(parsed.data);
      if (
        parsed.data.event === 'run.completed' ||
        parsed.data.event === 'run.failed' ||
        parsed.data.event === 'stage.waiting'
      ) {
        source.close();
      }
    });
  }

  source.addEventListener('heartbeat', () => handlers.onHeartbeat?.());
  source.addEventListener('error', () => {
    if (source.readyState === EventSource.CLOSED) return;
    handlers.onError(new Error('实时连接暂时中断，正在自动重连。'));
  });

  return { close: () => source.close() };
}
