'use client';

import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import { subscribeToRunEvents } from '../api/service';
import type { SpecialistView, StageEvent, StageName, StageView } from '../api/types';

const STAGE_ORDER: StageName[] = [
  'understanding',
  'planning',
  'analysis',
  'data_processing',
  'verification',
  'synthesis'
];

const STAGE_TITLES: Record<StageName, string> = {
  understanding: '理解问题',
  planning: '制定计划',
  analysis: '分析数据',
  data_processing: '处理数据',
  verification: '验证原因',
  synthesis: '整理结论',
  waiting_input: '等待补充信息',
  waiting_approval: '等待人工审批'
};

interface RunState {
  /**
   * Identity of the subscription this state belongs to. It changes when the run
   * changes and when the user restarts one; carrying it in the state is what
   * keeps a previous subscription from being rendered under its successor.
   */
  key: string;
  events: StageEvent[];
  connection: 'connecting' | 'live' | 'completed' | 'waiting' | 'error';
  error?: string;
  /** 模型思考过程的增量文本，按 unit_id（专家）或 stage 分块累积。 */
  reasonings: Record<string, string>;
}

type RunAction =
  | { type: 'reset'; key: string }
  | { type: 'open'; key: string }
  | { type: 'event'; key: string; event: StageEvent }
  | { type: 'error'; key: string; error: string };

function emptyState(key: string): RunState {
  return { key, events: [], connection: 'connecting', reasonings: {} };
}

function reducer(state: RunState, action: RunAction): RunState {
  // ``reset`` is how a new subscription claims the state, so it always applies.
  if (action.type === 'reset') return emptyState(action.key);
  // Everything else is dropped when it still carries a replaced subscription's
  // key, so a late event can never be attributed to the run that superseded it.
  if (action.key !== state.key) return state;
  if (action.type === 'open') return { ...state, connection: 'live', error: undefined };
  if (action.type === 'error') return { ...state, connection: 'error', error: action.error };

  const exists = state.events.some((event) => event.event_id === action.event.event_id);
  const events = exists ? state.events : [...state.events, action.event];
  let connection = state.connection;
  if (action.event.event === 'run.completed') connection = 'completed';
  if (action.event.event === 'run.failed') connection = 'error';
  if (action.event.event === 'stage.waiting') connection = 'waiting';

  let reasonings = state.reasonings;
  if (
    !exists &&
    action.event.event === 'stage.progress' &&
    action.event.data.kind === 'reasoning.delta' &&
    typeof action.event.data.text === 'string'
  ) {
    const reasoningKey =
      typeof action.event.data.unit_id === 'string'
        ? action.event.data.unit_id
        : (action.event.stage ?? 'unknown');
    reasonings = {
      ...state.reasonings,
      [reasoningKey]: (state.reasonings[reasoningKey] ?? '') + action.event.data.text
    };
  }
  return { ...state, events, connection, reasonings };
}

export function useRunStream(runId: string, enabled = true) {
  const [state, dispatch] = useReducer(reducer, emptyState(''));
  const [attempt, setAttempt] = useState(0);
  const key = `${runId}#${attempt}`;
  // Only the state belonging to the current subscription may render. Until the
  // `reset` below lands, `state` still describes the previous run — returning it
  // would paint the old answer under the new question for one frame. The
  // fallback is memoized so its arrays keep a stable identity between renders.
  const idle = useMemo(() => emptyState(key), [key]);
  const active = enabled && state.key === key ? state : idle;

  useEffect(() => {
    if (!enabled) return;
    // Claim the identity before subscribing; the reducer then ignores anything
    // that still carries the previous subscription's key.
    dispatch({ type: 'reset', key });
    const subscription = subscribeToRunEvents(runId, {
      onOpen: () => dispatch({ type: 'open', key }),
      onEvent: (event) => dispatch({ type: 'event', key, event }),
      onError: (error) => dispatch({ type: 'error', key, error: error.message })
    });
    return subscription.close;
  }, [runId, key, enabled]);

  const restart = useCallback(() => {
    // A new attempt makes the next render's key differ, which both resets the
    // state and resubscribes; no explicit reset dispatch is needed here.
    setAttempt((value) => value + 1);
  }, []);

  const stages = useMemo<StageView[]>(() => {
    return STAGE_ORDER.map((name) => {
      const relevant = active.events.filter((event) => event.stage === name);
      const started = relevant.some((event) => event.event === 'stage.started');
      const completed = relevant.find((event) => event.event === 'stage.completed');
      const waiting = relevant.some((event) => event.event === 'stage.waiting');
      const failed = relevant.some((event) => event.event === 'stage.failed');
      let status: StageView['status'] = 'pending';
      if (started) status = 'active';
      if (completed) status = 'completed';
      if (waiting) status = 'waiting';
      if (failed) status = 'failed';
      return {
        name,
        title: STAGE_TITLES[name],
        status,
        durationMs:
          typeof completed?.data.duration_ms === 'number' ? completed.data.duration_ms : undefined
      };
    });
  }, [active.events]);

  const specialists = useMemo<SpecialistView[]>(() => {
    const views = new Map<string, SpecialistView>();
    for (const event of active.events) {
      if (event.event !== 'stage.progress') continue;
      const kind = event.data.kind;
      const unitId = event.data.unit_id;
      if (typeof unitId !== 'string') continue;
      if (kind === 'unit.started') views.set(unitId, { id: unitId, status: 'running' });
      if (kind === 'tool.progress') {
        const current = views.get(unitId) ?? { id: unitId, status: 'running' };
        views.set(unitId, {
          ...current,
          recordsReceived:
            typeof event.data.records_received === 'number'
              ? event.data.records_received
              : current.recordsReceived
        });
      }
      if (
        kind === 'unit.completed' ||
        kind === 'unit.degraded' ||
        kind === 'unit.waiting' ||
        kind === 'unit.unavailable' ||
        kind === 'unit.failed'
      ) {
        views.set(unitId, {
          id: unitId,
          status:
            kind === 'unit.completed'
              ? 'completed'
              : kind === 'unit.degraded'
                ? 'degraded'
              : kind === 'unit.waiting'
                ? 'waiting'
                : kind === 'unit.unavailable'
                  ? 'unavailable'
                : 'failed',
          summary: typeof event.data.summary === 'string' ? event.data.summary : undefined
        });
      }
    }
    return Array.from(views.values());
  }, [active.events]);

  return {
    events: active.events,
    connection: active.connection,
    error: active.error,
    reasonings: active.reasonings,
    stages,
    specialists,
    restart
  };
}
