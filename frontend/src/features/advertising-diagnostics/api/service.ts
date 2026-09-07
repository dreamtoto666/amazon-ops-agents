"use client";

import {
  ADVERTISING_STAGE_EVENT_NAMES,
  type AdvertisingRunRecord,
  type AdvertisingShop,
  type AdvertisingSelectionDirectory,
  type AdvertisingStageEvent,
  type CreateAdvertisingRunInput,
} from "./types";
import { createUuid } from "@/lib/uuid";

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as T & {
    detail?: string;
  };
  if (!response.ok) {
    throw new Error(payload.detail ?? `请求失败（${response.status}）`);
  }
  return payload;
}

export async function getAdvertisingShops(): Promise<AdvertisingShop[]> {
  return readJson<AdvertisingShop[]>(
    await fetch("/api/ad-diagnostics/shops", { cache: "no-store" }),
  );
}

export async function getAdvertisingSelectionDirectory(): Promise<AdvertisingSelectionDirectory> {
  return readJson<AdvertisingSelectionDirectory>(
    await fetch('/api/ad-diagnostics/selection-directory', { cache: 'no-store' }),
  );
}

export async function createAdvertisingRun(
  input: CreateAdvertisingRunInput,
  idempotencyKey = createUuid(),
): Promise<{ run_id: string; trace_id: string; status: string }> {
  return readJson(
    await fetch("/api/ad-diagnostics/runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(input),
    }),
  );
}

export async function getAdvertisingRun(
  runId: string,
): Promise<AdvertisingRunRecord> {
  return readJson(
    await fetch(`/api/ad-diagnostics/runs/${encodeURIComponent(runId)}`, {
      cache: "no-store",
    }),
  );
}

export async function getAdvertisingHistory(): Promise<AdvertisingRunRecord[]> {
  return readJson(
    await fetch("/api/ad-diagnostics/history?limit=12", { cache: "no-store" }),
  );
}

export async function deleteAdvertisingHistory(runId: string): Promise<void> {
  await readJson(
    await fetch(`/api/ad-diagnostics/history/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    }),
  );
}

export function subscribeToAdvertisingRun(
  runId: string,
  handlers: {
    onOpen?: () => void;
    onEvent: (event: AdvertisingStageEvent) => void;
    onError: (error: Error) => void;
  },
): () => void {
  const source = new EventSource(
    `/api/ad-diagnostics/runs/${encodeURIComponent(runId)}/events`,
  );
  source.addEventListener("open", () => handlers.onOpen?.());
  for (const eventName of ADVERTISING_STAGE_EVENT_NAMES) {
    source.addEventListener(eventName, (message) => {
      try {
        const event = JSON.parse(
          (message as MessageEvent<string>).data,
        ) as AdvertisingStageEvent;
        handlers.onEvent(event);
        if (event.event === "run.completed" || event.event === "run.failed")
          source.close();
      } catch {
        handlers.onError(new Error("收到无法识别的巡检进度。"));
      }
    });
  }
  source.addEventListener("error", () => {
    if (source.readyState !== EventSource.CLOSED) {
      handlers.onError(new Error("巡检进度连接暂时中断。"));
    }
  });
  return () => source.close();
}
