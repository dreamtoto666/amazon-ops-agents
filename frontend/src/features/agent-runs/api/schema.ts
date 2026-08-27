import { z } from 'zod';
import { STAGE_EVENT_NAMES, STAGE_NAMES } from './types';

export const stageEventSchema = z.object({
  event_id: z.string(),
  run_id: z.string(),
  sequence: z.number().int().positive(),
  event: z.enum(STAGE_EVENT_NAMES),
  stage: z.enum(STAGE_NAMES).nullable(),
  timestamp: z.string(),
  data: z.record(z.string(), z.unknown())
});

