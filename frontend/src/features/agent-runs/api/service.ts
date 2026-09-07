'use client';

import { stageEventSchema } from './schema';
import { STAGE_EVENT_NAMES, type StageEvent } from './types';
import { createUuid } from '@/lib/uuid';

export interface RunEventHandlers {
  onOpen?: () => void;
  onEvent: (event: StageEvent) => void;
  onHeartbeat?: () => void;
  onError: (error: Error) => void;
}

export interface RunEventSubscription {
  close: () => void;
}

export type AgentModelName =
  | 'deepseek-v4-flash'
  | 'deepseek-v4-pro'
  | 'deepseek-v4-flash-vision-exp';

export type AgentReasoningEffort = 'off' | 'low' | 'high' | 'max';

export interface CreateRunResult {
  run_id: string;
  conversation_id: string;
  status: string;
  model: AgentModelName;
  reasoning_effort?: AgentReasoningEffort;
}

export interface ConversationSummary {
  conversation_id: string;
  preview: string;
}

export interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
  kind: 'message' | 'summary';
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail ?? `会话请求失败（${response.status}）`);
  }
  return payload;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  return readJson<ConversationSummary[]>(await fetch('/api/agent/conversations'));
}

export async function getConversationMessages(
  conversationId: string
): Promise<ConversationMessage[]> {
  return readJson<ConversationMessage[]>(
    await fetch(`/api/agent/conversations/${encodeURIComponent(conversationId)}/messages`)
  );
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(
    `/api/agent/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' }
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(payload.detail ?? `删除对话失败（${response.status}）`);
  }
}

export async function createAgentRun(
  message: string,
  conversationId: string,
  model: AgentModelName,
  reasoningEffort: AgentReasoningEffort = 'off',
  idempotencyKey = createUuid(),
  imageDataUrls: string[] = []
): Promise<CreateRunResult> {
  const response = await fetch('/api/agent/runs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      model,
      reasoning_effort: reasoningEffort,
      image_attachments: imageDataUrls.map((dataUrl) => ({ data_url: dataUrl }))
    })
  });
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: string;
    run_id?: string;
    conversation_id?: string;
    status?: string;
    model?: AgentModelName;
    reasoning_effort?: AgentReasoningEffort;
  };
  if (!response.ok || !payload.run_id || !payload.conversation_id) {
    throw new Error(payload.detail ?? `Agent 请求失败（${response.status}）`);
  }
  return {
    run_id: payload.run_id,
    conversation_id: payload.conversation_id,
    status: payload.status ?? 'accepted',
    model: payload.model ?? model,
    reasoning_effort: payload.reasoning_effort ?? reasoningEffort
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
