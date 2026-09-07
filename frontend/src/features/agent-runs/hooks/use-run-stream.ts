'use client';

import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import { subscribeToRunEvents } from '../api/service';
import type { SpecialistView, StageEvent, StageName, StageView } from '../api/types';

const STAGE_ORDER: StageName[] = [
  'understanding',
  'planning',
  'analysis',
  'verification',
  'synthesis'
];

const STAGE_TITLES: Record<StageName, string> = {
  understanding: '理解问题',
  planning: '制定计划',
  analysis: '分析数据',
  verification: '验证原因',
  synthesis: '整理结论',
  waiting_input: '等待补充信息',
  waiting_approval: '等待人工审批'
};

interface RunState {
  events: StageEvent[];
  connection: 'connecting' | 'live' | 'completed' | 'waiting' | 'error';
  error?: string;
  /** 模型思考过程的增量文本，按 unit_id（专家）或 stage 分块累积。 */
  reasonings: Record<string, string>;
}

type RunAction =
  | { type: 'open' }
  | { type: 'event'; event: StageEvent }
  | { type: 'error'; error: string }
  | { type: 'reset' };

const initialState: RunState = { events: [], connection: 'connecting', reasonings: {} };

function reducer(state: RunState, action: RunAction): RunState {
  if (action.type === 'reset') return initialState;
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
    const key =
      typeof action.event.data.unit_id === 'string'
        ? action.event.data.unit_id
        : action.event.stage ?? 'unknown';
    reasonings = {
      ...state.reasonings,
      [key]: (state.reasonings[key] ?? '') + action.event.data.text
    };
  }
  return { ...state, events, connection, reasonings };
}

export function useRunStream(runId: string, enabled = true) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    dispatch({ type: 'reset' });
    const subscription = subscribeToRunEvents(runId, {
      onOpen: () => dispatch({ type: 'open' }),
      onEvent: (event) => dispatch({ type: 'event', event }),
      onError: (error) => dispatch({ type: 'error', error: error.message })
    });
    return subscription.close;
  }, [runId, attempt, enabled]);

  const restart = useCallback(() => {
    dispatch({ type: 'reset' });
    setAttempt((value) => value + 1);
  }, []);

  const stages = useMemo<StageView[]>(() => {
    return STAGE_ORDER.map((name) => {
      const relevant = state.events.filter((event) => event.stage === name);
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
  }, [state.events]);

  const specialists = useMemo<SpecialistView[]>(() => {
    const views = new Map<string, SpecialistView>();
    for (const event of state.events) {
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
      if (kind === 'unit.completed' || kind === 'unit.waiting' || kind === 'unit.failed') {
        views.set(unitId, {
          id: unitId,
          status:
            kind === 'unit.completed'
              ? 'completed'
              : kind === 'unit.waiting'
                ? 'waiting'
                : 'failed',
          summary: typeof event.data.summary === 'string' ? event.data.summary : undefined
        });
      }
    }
    return Array.from(views.values());
  }, [state.events]);

  return {
    ...state,
    reasonings: state.reasonings ?? {},
    stages,
    specialists,
    restart
  };
}
