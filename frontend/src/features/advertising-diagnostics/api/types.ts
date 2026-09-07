export interface AdvertisingShop {
  profile_id: string;
  sid: number | null;
  store_id: number | null;
  alias: string;
  country: string | null;
  marketplace_id: string | null;
  account_type: string | null;
}

export interface AdvertisingSelectionProduct {
  product_ref: string;
  parent_asin: string;
}

export interface AdvertisingSelectionResponsible {
  responsible_ref: string;
  label: string;
  products: AdvertisingSelectionProduct[];
}

export interface AdvertisingSelectionStore {
  shop_ref: string;
  label: string;
  responsibles: AdvertisingSelectionResponsible[];
}

export interface AdvertisingSelectionDirectory {
  version: string;
  stores: AdvertisingSelectionStore[];
}

export interface DiagnosticPeriod {
  start: string;
  end: string;
}

export interface CreateAdvertisingRunInput {
  selection_version: string;
  shop_ref: string;
  product_refs: string[];
  current_period: DiagnosticPeriod;
  baseline_period?: DiagnosticPeriod;
  goal: { growth_priority: "profit" | "balanced" | "scale" };
  trigger: "manual";
}

export interface AdEntityRef {
  entity_type: string;
  entity_id: string;
  name: string | null;
  profile_id: string;
  campaign_id: string | null;
  ad_group_id: string | null;
  asin: string | null;
  sku: string | null;
}

export interface AdvertisingAnomaly {
  anomaly_id: string;
  anomaly_type: string;
  entity: AdEntityRef;
  metric: string;
  current_value: number | null;
  baseline_value: number | null;
  absolute_change: number | null;
  relative_change: number | null;
  severity: "low" | "medium" | "high" | "critical";
  confidence: number;
  evidence_refs: string[];
}

export interface AdvertisingTodo {
  todo_id: string;
  title: string;
  description: string;
  priority: "low" | "medium" | "high" | "urgent";
  status: "pending" | "needs_review" | "approved" | "rejected" | "done";
  action_type: string;
  target: AdEntityRef;
  proposed_change: Record<string, unknown>;
  evidence_refs: string[];
  detail?: {
    diagnosis: string;
    metric_summary: string;
    attribution: string;
    checked_sources?: string[];
    recommendation: string;
    expected_effect: string | null;
  } | null;
  approval_required: boolean;
  due_at: string | null;
}

export interface AdvertisingEvidence {
  evidence_id: string;
  trace_id: string;
  span_id: string;
  stage: string;
  source: string;
  tool: string;
  query: Record<string, unknown>;
  fetched_at: string;
  record_count: number;
  entity_refs: string[];
  artifact_id: string | null;
}

export interface AdvertisingDiagnosticResult {
  trace_id: string;
  run_id: string;
  span_id: string;
  stage: string;
  status: "completed" | "no_anomaly" | "needs_review" | "failed";
  summary: string;
  anomalies: AdvertisingAnomaly[];
  todos: AdvertisingTodo[];
  evidence: AdvertisingEvidence[];
  warnings: string[];
}

export interface AdvertisingRunRecord {
  run_id: string;
  trace_id: string;
  span_id: string;
  stage: string;
  status: string;
  result: AdvertisingDiagnosticResult | null;
  error: { code?: string; message?: string } | null;
  created_at?: string | null;
  display_scope?: {
    shop_label: string;
    campaign_count: number;
    current_period?: DiagnosticPeriod;
  } | null;
}

export const ADVERTISING_STAGE_EVENT_NAMES = [
  "run.started",
  "stage.started",
  "stage.progress",
  "stage.completed",
  "stage.failed",
  "run.completed",
  "run.failed",
] as const;

export type AdvertisingStage =
  | "data_inspection"
  | "problem_attribution"
  | "strategy_recommendation"
  | "review_todo";

export interface AdvertisingStageEvent {
  event_id: string;
  run_id: string;
  trace_id: string | null;
  span_id: string | null;
  sequence: number;
  event: (typeof ADVERTISING_STAGE_EVENT_NAMES)[number];
  stage: AdvertisingStage | null;
  timestamp: string;
  data: Record<string, unknown>;
}
