export const STAGE_NAMES = [
  'understanding',
  'planning',
  'analysis',
  'verification',
  'synthesis',
  'waiting_input',
  'waiting_approval'
] as const;

export const STAGE_EVENT_NAMES = [
  'run.started',
  'stage.started',
  'stage.progress',
  'stage.completed',
  'stage.waiting',
  'stage.failed',
  'run.completed',
  'run.failed'
] as const;

export type StageName = (typeof STAGE_NAMES)[number];
export type StageEventName = (typeof STAGE_EVENT_NAMES)[number];

export interface StageEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  event: StageEventName;
  stage: StageName | null;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface StageView {
  name: StageName;
  status: 'pending' | 'active' | 'completed' | 'waiting' | 'failed';
  title: string;
  summary?: string;
  durationMs?: number;
}

export interface SpecialistView {
  id: string;
  status: 'running' | 'completed' | 'waiting' | 'failed';
  summary?: string;
  recordsReceived?: number;
}
