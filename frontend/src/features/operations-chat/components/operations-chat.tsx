'use client';

import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from 'react';
import { Icons } from '@/components/icons';
import { Badge } from '@/components/ui/badge';
import { Bubble, BubbleContent } from '@/components/ui/bubble';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Message,
  MessageAvatar,
  MessageContent,
  MessageFooter,
  MessageHeader
} from '@/components/ui/message';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import { createAgentRun } from '@/features/agent-runs/api/service';
import { useRunStream } from '@/features/agent-runs/hooks/use-run-stream';
import { cn } from '@/lib/utils';

const SPECIALIST_LABELS: Record<string, string> = {
  sales_profit: '销售利润 Agent',
  advertising: '广告诊断 Agent',
  inventory: '库存补货 Agent',
  market_risk: '市场风险 Agent',
  listing_content: 'Listing 文案 Agent'
};

const CONVERSATION_STORAGE_KEY = 'amazon-ops-conversation-id';

function createConversationId() {
  return `conversation-${crypto.randomUUID().replaceAll('-', '')}`;
}

interface SystemHealth {
  status: string;
  llm: { provider: string; model: string; configured: boolean };
  specialists: string[];
  mcp: { seller_sprite_configured: boolean; sif_configured: boolean };
}

interface FinalResponseData {
  answer?: string;
  confirmed_findings?: Array<{ finding?: string; severity?: string }>;
  open_hypotheses?: Array<{ description?: string }>;
  recommended_actions?: Array<{ action?: string; risk_level?: string }>;
  deliverables?: Array<{
    type?: string;
    draft?: {
      title?: string;
      subtitle?: string;
      bullet_points?: string[];
      description?: string;
      search_terms?: string;
    };
  }>;
}

type RunStream = ReturnType<typeof useRunStream>;

function StageStatusIcon({ status }: { status: string }) {
  if (status === 'completed') return <Icons.circleCheck className='size-4 text-emerald-600' />;
  if (status === 'active') return <Icons.spinner className='size-4 animate-spin text-primary' />;
  if (status === 'failed') return <Icons.warning className='size-4 text-destructive' />;
  return <Icons.circle className='size-4 text-muted-foreground/50' />;
}

function ResultContent({ result }: { result?: FinalResponseData }) {
  if (!result?.answer) {
    return <p>任务已结束，但系统没有返回可展示的答案。</p>;
  }
  return (
    <div className='space-y-4'>
      <p className='whitespace-pre-wrap leading-relaxed'>{result.answer}</p>
      {result.confirmed_findings && result.confirmed_findings.length > 0 && (
        <div>
          <p className='mb-1 text-xs font-semibold text-muted-foreground'>已确认发现</p>
          <ul className='space-y-1 text-sm'>
            {result.confirmed_findings.map((item, index) => (
              <li key={`${item.finding ?? 'finding'}-${index}`}>• {item.finding}</li>
            ))}
          </ul>
        </div>
      )}
      {result.open_hypotheses && result.open_hypotheses.length > 0 && (
        <div>
          <p className='mb-1 text-xs font-semibold text-muted-foreground'>待验证假设</p>
          <ul className='space-y-1 text-sm'>
            {result.open_hypotheses.map((item, index) => (
              <li key={`${item.description ?? 'hypothesis'}-${index}`}>• {item.description}</li>
            ))}
          </ul>
        </div>
      )}
      {result.recommended_actions && result.recommended_actions.length > 0 && (
        <div>
          <p className='mb-1 text-xs font-semibold text-muted-foreground'>建议动作</p>
          <ul className='space-y-1 text-sm'>
            {result.recommended_actions.map((item, index) => (
              <li key={`${item.action ?? 'action'}-${index}`}>• {item.action}</li>
            ))}
          </ul>
        </div>
      )}
      {result.deliverables?.map((deliverable, index) =>
        deliverable.type === 'listing_draft' && deliverable.draft ? (
          <div key={`listing-${index}`} className='space-y-3 rounded-xl border bg-muted/20 p-4'>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>主标题</p>
              <p className='mt-1'>{deliverable.draft.title}</p>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>副标题</p>
              <p className='mt-1'>{deliverable.draft.subtitle}</p>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>五点描述</p>
              <ul className='mt-1 space-y-1 text-sm'>
                {deliverable.draft.bullet_points?.map((bullet, bulletIndex) => (
                  <li key={`${bullet}-${bulletIndex}`}>• {bullet}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>商品描述</p>
              <p className='mt-1 whitespace-pre-wrap text-sm'>{deliverable.draft.description}</p>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>Search Terms</p>
              <p className='mt-1 text-sm'>{deliverable.draft.search_terms}</p>
            </div>
          </div>
        ) : null
      )}
    </div>
  );
}

function AnalysisRun({ run }: { run: RunStream }) {
  const { connection, specialists, stages } = run;
  const completed = stages.filter((stage) => stage.status === 'completed').length;
  const progress = Math.round((completed / stages.length) * 100);
  const completedEvent = run.events.toReversed().find((event) => event.event === 'run.completed');
  const failedEvent = run.events.toReversed().find((event) => event.event === 'run.failed');
  const waitingEvent = run.events.toReversed().find((event) => event.event === 'stage.waiting');
  const result = completedEvent?.data.result as FinalResponseData | undefined;
  const failure =
    (typeof failedEvent?.data.error === 'string' ? failedEvent.data.error : undefined) ?? run.error;
  const waitingMessage =
    typeof waitingEvent?.data.question === 'string'
      ? waitingEvent.data.question
      : typeof waitingEvent?.data.message === 'string'
        ? waitingEvent.data.message
        : '任务正在等待补充信息或审批。';

  return (
    <Message>
      <MessageAvatar className='bg-primary text-primary-foreground size-8'>
        <Icons.sparkles className='size-4' />
      </MessageAvatar>
      <MessageContent>
        <MessageHeader>Amazon Ops 总控</MessageHeader>
        <Bubble variant='outline' className='max-w-[92%]'>
          <BubbleContent className='w-full min-w-[280px] space-y-3'>
            {connection === 'completed' ? (
              <ResultContent result={result} />
            ) : connection === 'waiting' ? (
              <div className='flex gap-2 text-sm'>
                <Icons.info className='mt-0.5 size-4 shrink-0 text-amber-600' />
                <p>{waitingMessage}</p>
              </div>
            ) : connection === 'error' ? (
              <div className='flex gap-2 text-sm text-destructive'>
                <Icons.warning className='mt-0.5 size-4 shrink-0' />
                <p>{failure ?? '真实 Agent 运行失败。'}</p>
              </div>
            ) : (
              <div className='space-y-3'>
                <div className='flex items-center justify-between gap-4'>
                  <span className='font-medium'>正在执行真实 Agent 任务…</span>
                  <span className='text-xs tabular-nums text-muted-foreground'>{progress}%</span>
                </div>
                <Progress value={progress} aria-label={`分析进度 ${progress}%`} />
                <ol className='grid grid-cols-2 gap-1.5 sm:grid-cols-5' aria-label='分析阶段'>
                  {stages.map((stage) => (
                    <li
                      key={stage.name}
                      className={cn(
                        'flex items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1.5 text-[11px] text-muted-foreground',
                        stage.status === 'active' && 'bg-primary/10 font-medium text-primary',
                        stage.status === 'completed' && 'text-foreground'
                      )}
                    >
                      <StageStatusIcon status={stage.status} />
                      <span>{stage.title}</span>
                    </li>
                  ))}
                </ol>
                {specialists.length > 0 && (
                  <div className='flex flex-wrap gap-1.5'>
                    {specialists.map((specialist) => (
                      <Badge key={specialist.id} variant='outline'>
                        {specialist.status === 'running' ? (
                          <Icons.spinner className='animate-spin' />
                        ) : specialist.status === 'completed' ? (
                          <Icons.circleCheck className='text-emerald-600' />
                        ) : specialist.status === 'waiting' ? (
                          <Icons.clock className='text-amber-600' />
                        ) : (
                          <Icons.warning className='text-destructive' />
                        )}
                        {SPECIALIST_LABELS[specialist.id] ?? specialist.id}
                        {specialist.recordsReceived ? ` · ${specialist.recordsReceived} 条` : ''}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            )}
          </BubbleContent>
        </Bubble>
        <MessageFooter>
          {connection === 'completed'
            ? '已完成'
            : connection === 'waiting'
              ? '等待你继续操作'
              : connection === 'error'
                ? '执行失败'
                : '阶段事件实时更新'}
        </MessageFooter>
      </MessageContent>
    </Message>
  );
}

export function OperationsChat() {
  const [input, setInput] = useState('');
  const [question, setQuestion] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string>();
  const [health, setHealth] = useState<SystemHealth>();
  const [healthError, setHealthError] = useState(false);
  const [conversationId, setConversationId] = useState<string>();
  const run = useRunStream(runId ?? 'idle', Boolean(runId));

  useEffect(() => {
    const stored = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
    const current = stored ?? createConversationId();
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, current);
    setConversationId(current);
  }, []);

  useEffect(() => {
    let active = true;
    fetch('/api/agent/health', { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('health unavailable');
        return (await response.json()) as SystemHealth;
      })
      .then((value) => {
        if (active) setHealth(value);
      })
      .catch(() => {
        if (active) setHealthError(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const statusLabel = useMemo(() => {
    if (healthError) return '后端未连接';
    if (!health) return '正在检测系统';
    return health.llm.configured
      ? `DeepSeek 已配置 · ${health.llm.model}`
      : 'DeepSeek API Key 未配置';
  }, [health, healthError]);

  async function submitQuestion(event?: FormEvent) {
    event?.preventDefault();
    const value = input.trim();
    if (!value || submitting || !conversationId) return;
    setQuestion(value);
    setRunId(undefined);
    setSubmitError(undefined);
    setSubmitting(true);
    setInput('');
    try {
      const created = await createAgentRun(value, conversationId);
      window.localStorage.setItem(CONVERSATION_STORAGE_KEY, created.conversation_id);
      setRunId(created.run_id);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '无法创建 Agent 任务。');
    } finally {
      setSubmitting(false);
    }
  }

  function resetChat() {
    const nextConversationId = createConversationId();
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, nextConversationId);
    setConversationId(nextConversationId);
    setQuestion(undefined);
    setRunId(undefined);
    setInput('');
    setSubmitError(undefined);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  return (
    <Card className='grid min-h-[680px] overflow-hidden p-0 lg:h-[calc(100vh-10.5rem)] lg:min-h-[620px] lg:grid-cols-[230px_minmax(0,1fr)]'>
      <aside className='hidden min-h-0 border-r bg-muted/20 lg:flex lg:flex-col'>
        <div className='flex items-center justify-between p-4'>
          <p className='text-sm font-semibold'>当前对话</p>
          <Button size='icon-sm' variant='ghost' aria-label='新建对话' onClick={resetChat}>
            <Icons.add />
          </Button>
        </div>
        <div className='px-3 pb-2'>
          <Button className='w-full justify-start' variant='outline' onClick={resetChat}>
            <Icons.edit /> 新建对话
          </Button>
        </div>
        <div className='min-h-0 flex-1 px-3 py-3'>
          {question ? (
            <div className='rounded-lg bg-muted px-3 py-2.5 text-sm'>
              <span className='block truncate font-medium'>{question}</span>
              <span className='mt-1 block text-xs text-muted-foreground'>
                {runId ?? (submitError ? '创建失败' : '正在创建任务')}
              </span>
            </div>
          ) : (
            <p className='px-1 text-xs leading-relaxed text-muted-foreground'>
              暂无对话。本页不再显示虚构历史；对话持久化完成后才会展示真实记录。
            </p>
          )}
        </div>
        <div className='border-t p-3 text-xs text-muted-foreground'>
          <div className='flex items-start gap-2 rounded-lg bg-background p-2'>
            {health?.llm.configured ? (
              <Icons.circleCheck className='mt-0.5 size-4 shrink-0 text-emerald-600' />
            ) : (
              <Icons.warning className='mt-0.5 size-4 shrink-0 text-amber-600' />
            )}
            <span>{statusLabel}</span>
          </div>
          {health && (
            <p className='mt-2 px-2'>已注册专业 Agent：{health.specialists.length}</p>
          )}
        </div>
      </aside>

      <section className='flex min-h-0 flex-col bg-background'>
        <div className='min-h-0 flex-1 overflow-y-auto'>
          <div className='mx-auto flex min-h-full max-w-4xl flex-col gap-6 px-4 py-6 sm:px-6'>
            <Message>
              <MessageAvatar className='size-8 bg-primary text-primary-foreground'>
                <Icons.sparkles className='size-4' />
              </MessageAvatar>
              <MessageContent>
                <MessageHeader>Amazon Ops 总控</MessageHeader>
                <Bubble variant='muted' className='max-w-[92%]'>
                  <BubbleContent>
                    请输入你的真实运营问题。系统只展示 DeepSeek、专业 Agent 和数据工具实际返回的结果；未配置的能力会如实报错。
                  </BubbleContent>
                </Bubble>
              </MessageContent>
            </Message>

            {question && (
              <Message align='end'>
                <MessageAvatar className='size-8 bg-secondary'>
                  <Icons.user className='size-4' />
                </MessageAvatar>
                <MessageContent>
                  <MessageHeader>你</MessageHeader>
                  <Bubble>
                    <BubbleContent>{question}</BubbleContent>
                  </Bubble>
                </MessageContent>
              </Message>
            )}

            {submitting && (
              <p className='pl-10 text-sm text-muted-foreground'>正在向后端创建真实任务…</p>
            )}
            {submitError && (
              <div className='ml-10 flex max-w-2xl gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive'>
                <Icons.warning className='mt-0.5 size-4 shrink-0' />
                <span>{submitError}</span>
              </div>
            )}
            {runId && <AnalysisRun key={runId} run={run} />}
          </div>
        </div>

        <form onSubmit={submitQuestion} className='border-t bg-background p-3 sm:p-4'>
          <div className='mx-auto max-w-4xl rounded-xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/30'>
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder='输入需要查询、分析或解释的运营问题'
              aria-label='向运营助手提问'
              className='max-h-32 min-h-14 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0'
            />
            <div className='flex items-center justify-end px-1 pt-1'>
              <Button
                type='submit'
                size='icon'
                disabled={!input.trim() || submitting || !conversationId}
                aria-label='发送问题'
              >
                {submitting ? <Icons.spinner className='animate-spin' /> : <Icons.send />}
              </Button>
            </div>
          </div>
          <p className='mx-auto mt-2 max-w-4xl text-center text-[11px] text-muted-foreground'>
            仅展示真实系统返回结果。执行经营动作前仍需核对原始证据。
          </p>
        </form>
      </section>
    </Card>
  );
}
