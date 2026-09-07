'use client';

import {
  ClipboardEvent,
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState
} from 'react';
import { Icons } from '@/components/icons';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  type ConversationMessage,
  type ConversationSummary,
  createAgentRun,
  deleteConversation,
  getConversationMessages,
  listConversations
} from '@/features/agent-runs/api/service';
import { useRunStream } from '@/features/agent-runs/hooks/use-run-stream';
import { createUuid } from '@/lib/uuid';
import { AdvertisingReportImport } from './advertising-report-import';
import { MarkdownContent } from './markdown-content';

const CONVERSATION_STORAGE_KEY = 'amazon-ops-conversation-id';
type ModelName = 'deepseek-v4-flash' | 'deepseek-v4-pro' | 'deepseek-v4-flash-vision-exp';
type ReasoningEffort = 'off' | 'low' | 'high' | 'max';

interface ImageAttachment {
  file: File;
  previewUrl: string;
}

const MAX_IMAGE_ATTACHMENTS = 4;
const MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024;

function createConversationId() {
  return `conversation-${createUuid().replaceAll('-', '')}`;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('图片读取失败，请重新粘贴。'));
    reader.readAsDataURL(file);
  });
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

function ExecutionTimeline({ events }: { events: RunStream['events'] }) {
  const auditEvents = events.filter(
    (event) =>
      event.event === 'stage.progress' &&
      ['tool.call.started', 'tool.call.completed', 'tool.call.failed'].includes(
        String(event.data.kind)
      )
  );
  if (auditEvents.length === 0) return null;

  return (
    <details className='rounded-lg border bg-muted/20 p-3 text-sm' open>
      <summary className='cursor-pointer font-medium'>执行过程</summary>
      <ol className='mt-3 space-y-3 border-l pl-3'>
        {auditEvents.map((event) => {
          const kind = String(event.data.kind);
          const tool = typeof event.data.tool === 'string' ? event.data.tool : '';
          if (kind === 'tool.call.started') {
            return <li key={event.event_id}>正在调用只读工具：{tool}</li>;
          }
          if (kind === 'tool.call.failed') {
            return (
              <li key={event.event_id} className='text-destructive'>
                工具调用失败：{tool}
              </li>
            );
          }
          const result = event.data.result;
          return (
            <li key={event.event_id}>
              <p>工具调用完成：{tool}</p>
              <details className='mt-1'>
                <summary className='cursor-pointer text-xs text-muted-foreground'>
                  查看脱敏业务结果
                </summary>
                <pre className='mt-2 max-h-64 overflow-auto rounded bg-background p-2 text-xs whitespace-pre-wrap break-all'>
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            </li>
          );
        })}
      </ol>
    </details>
  );
}

const REASONING_TITLES: Record<string, string> = {
  understanding: '理解问题',
  planning: '制定计划',
  analysis: '分析数据',
  verification: '验证原因',
  synthesis: '整理结论',
  advertising: '广告专家',
  waiting_input: '等待补充信息',
  waiting_approval: '等待人工审批'
};

function ThinkingBlocks({ reasonings }: { reasonings: Record<string, string> }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const entries = Object.entries(reasonings ?? {});
  if (entries.length === 0) return null;
  return (
    <div className='space-y-2'>
      {entries.map(([key, text]) => (
        <details
          key={key}
          className='rounded-lg border bg-muted/20 p-2 text-xs'
          open={expanded[key] ?? true}
        >
          <summary
            className='cursor-pointer font-medium text-muted-foreground'
            onClick={(event) => {
              event.preventDefault();
              setExpanded((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
            }}
          >
            <Icons.sparkles className='mr-1 inline size-3.5' />
            思考过程
            {REASONING_TITLES[key] ? ' · ' + REASONING_TITLES[key] : ''}
          </summary>
          <p className='mt-2 leading-relaxed whitespace-pre-wrap text-muted-foreground'>
            {text}
          </p>
        </details>
      ))}
    </div>
  );
}

function ResultContent({ result }: { result?: FinalResponseData }) {
  if (!result?.answer) {
    return <p>任务已结束，但系统没有返回可展示的答案。</p>;
  }
  return (
    <div className='space-y-4'>
      <MarkdownContent content={result.answer} />
      {result.confirmed_findings && result.confirmed_findings.length > 0 && (
        <div>
          <p className='mb-1 text-xs font-semibold text-muted-foreground'>
            已确认发现
          </p>
          <ul className='space-y-1 text-sm'>
            {result.confirmed_findings.map((item, index) => (
              <li key={`${item.finding ?? 'finding'}-${index}`}>
                • {item.finding}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.open_hypotheses && result.open_hypotheses.length > 0 && (
        <div>
          <p className='mb-1 text-xs font-semibold text-muted-foreground'>
            待验证假设
          </p>
          <ul className='space-y-1 text-sm'>
            {result.open_hypotheses.map((item, index) => (
              <li key={`${item.description ?? 'hypothesis'}-${index}`}>
                • {item.description}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.recommended_actions && result.recommended_actions.length > 0 && (
        <div>
          <p className='mb-1 text-xs font-semibold text-muted-foreground'>
            建议动作
          </p>
          <ul className='space-y-1 text-sm'>
            {result.recommended_actions.map((item, index) => (
              <li key={`${item.action ?? 'action'}-${index}`}>
                • {item.action}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.deliverables?.map((deliverable, index) =>
        deliverable.type === 'listing_draft' && deliverable.draft ? (
          <div
            key={`listing-${index}`}
            className='space-y-3 rounded-xl border bg-muted/20 p-4'
          >
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>
                主标题
              </p>
              <p className='mt-1'>{deliverable.draft.title}</p>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>
                副标题
              </p>
              <p className='mt-1'>{deliverable.draft.subtitle}</p>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>
                五点描述
              </p>
              <ul className='mt-1 space-y-1 text-sm'>
                {deliverable.draft.bullet_points?.map((bullet, bulletIndex) => (
                  <li key={`${bullet}-${bulletIndex}`}>• {bullet}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>
                商品描述
              </p>
              <p className='mt-1 whitespace-pre-wrap text-sm'>
                {deliverable.draft.description}
              </p>
            </div>
            <div>
              <p className='text-xs font-semibold text-muted-foreground'>
                Search Terms
              </p>
              <p className='mt-1 text-sm'>{deliverable.draft.search_terms}</p>
            </div>
          </div>
        ) : null
      )}
    </div>
  );
}

function AnalysisRun({ run }: { run: RunStream }) {
  const { connection } = run;
  const completedEvent = run.events
    .toReversed()
    .find((event) => event.event === 'run.completed');
  const failedEvent = run.events
    .toReversed()
    .find((event) => event.event === 'run.failed');
  const waitingEvent = run.events
    .toReversed()
    .find((event) => event.event === 'stage.waiting');
  const result = completedEvent?.data.result as FinalResponseData | undefined;
  const streamedAnswer = run.events
    .filter(
      (event) =>
        event.event === 'stage.progress' &&
        event.data.kind === 'response.delta' &&
        typeof event.data.text === 'string'
    )
    .map((event) => event.data.text as string)
    .join('');
  const failure =
    (typeof failedEvent?.data.error === 'string'
      ? failedEvent.data.error
      : undefined) ?? run.error;
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
            <ThinkingBlocks reasonings={run.reasonings} />
            {connection === 'completed' ? (
              <>
                <ExecutionTimeline events={run.events} />
                <ResultContent result={result} />
              </>
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
                <div className='flex items-center gap-2 font-medium'>
                  <Icons.spinner className='size-4 animate-spin text-primary' />
                  正在执行任务…
                </div>
                <ExecutionTimeline events={run.events} />
                {streamedAnswer && (
                  <div className='rounded-lg border bg-background p-3 leading-relaxed whitespace-pre-wrap'>
                    {streamedAnswer}
                    <span className='ml-1 inline-block size-2 animate-pulse rounded-full bg-primary' />
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
  const [model, setModel] = useState<ModelName>('deepseek-v4-flash');
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>('off');
  const [question, setQuestion] = useState<string>();
  const [questionImages, setQuestionImages] = useState<ImageAttachment[]>([]);
  const [imageAttachments, setImageAttachments] = useState<ImageAttachment[]>([]);
  const [runId, setRunId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string>();
  const [conversationId, setConversationId] = useState<string>();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [history, setHistory] = useState<ConversationMessage[]>([]);
  const [historyError, setHistoryError] = useState<string>();
  const [deletingConversationId, setDeletingConversationId] = useState<string>();
  const isComposingRef = useRef(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const run = useRunStream(runId ?? 'idle', Boolean(runId));

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : '无法读取会话记录。'
      );
    }
  }, []);

  useEffect(() => {
    const stored = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
    const current = stored ?? createConversationId();
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, current);
    setConversationId(current);
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  // Auto-scroll to the latest message while streaming, but only when the user
  // is already near the bottom so an in-progress scroll-up is not yanked back.
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [run.events, run.connection, question, submitting]);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    setHistory([]);
    setHistoryError(undefined);
    void getConversationMessages(conversationId)
      .then((messages) => {
        if (!cancelled) setHistory(messages);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setHistoryError(
            error instanceof Error ? error.message : '无法读取当前对话。'
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.items)
      .filter((item) => item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (files.length === 0) return;

    const accepted = files.filter((file) => file.size <= MAX_IMAGE_SIZE_BYTES);
    if (accepted.length !== files.length) {
      setSubmitError('单张图片不能超过 5 MB。');
    }
    setImageAttachments((current) => {
      const remaining = MAX_IMAGE_ATTACHMENTS - current.length;
      const nextFiles = accepted.slice(0, Math.max(remaining, 0));
      if (nextFiles.length < accepted.length) {
        setSubmitError(`一次最多可附加 ${MAX_IMAGE_ATTACHMENTS} 张图片。`);
      }
      return [
        ...current,
        ...nextFiles.map((file) => ({
          file,
          previewUrl: URL.createObjectURL(file)
        }))
      ];
    });
    setModel('deepseek-v4-flash-vision-exp');
  }

  function removeImageAttachment(previewUrl: string) {
    setImageAttachments((current) => {
      const attachment = current.find((item) => item.previewUrl === previewUrl);
      if (attachment) URL.revokeObjectURL(attachment.previewUrl);
      return current.filter((item) => item.previewUrl !== previewUrl);
    });
  }

  async function submitQuestion(event?: FormEvent) {
    event?.preventDefault();
    const value = input.trim();
    if ((!value && imageAttachments.length === 0) || submitting || !conversationId) return;
    const message = value || '请分析这张图片。';
    const attachments = imageAttachments;
    setQuestion(message);
    setQuestionImages(attachments);
    setRunId(undefined);
    setSubmitError(undefined);
    setSubmitting(true);
    setInput('');
    setImageAttachments([]);
    try {
      const imageDataUrls = await Promise.all(
        attachments.map((attachment) => readFileAsDataUrl(attachment.file))
      );
      const created = await createAgentRun(
        message,
        conversationId,
        model,
        reasoningEffort,
        undefined,
        imageDataUrls
      );
      window.localStorage.setItem(
        CONVERSATION_STORAGE_KEY,
        created.conversation_id
      );
      setRunId(created.run_id);
      void refreshConversations();
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : '无法创建 Agent 任务。'
      );
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
    setImageAttachments([]);
    setQuestionImages([]);
    setSubmitError(undefined);
    setHistory([]);
    setHistoryError(undefined);
  }

  function selectConversation(nextConversationId: string) {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, nextConversationId);
    setConversationId(nextConversationId);
    setQuestion(undefined);
    setRunId(undefined);
    setInput('');
    setImageAttachments([]);
    setQuestionImages([]);
    setSubmitError(undefined);
  }

  async function removeConversation(targetConversationId: string) {
    if (
      targetConversationId === conversationId &&
      Boolean(runId) &&
      (run.connection === 'connecting' || run.connection === 'live')
    ) {
      setSubmitError('当前任务仍在运行，请等待任务结束后再删除此对话。');
      return;
    }
    if (!window.confirm('删除此对话及其中所有消息？此操作无法撤销。')) {
      return;
    }
    setDeletingConversationId(targetConversationId);
    setSubmitError(undefined);
    try {
      await deleteConversation(targetConversationId);
      if (targetConversationId === conversationId) {
        resetChat();
      }
      await refreshConversations();
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : '无法删除此对话。'
      );
    } finally {
      setDeletingConversationId(undefined);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const isImeComposing =
      event.nativeEvent.isComposing ||
      event.keyCode === 229 ||
      isComposingRef.current;

    if (isImeComposing) {
      return;
    }

    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  return (
    <Card className='grid min-h-[680px] overflow-hidden p-0 lg:h-[calc(100vh-10.5rem)] lg:min-h-[620px] lg:grid-cols-[230px_minmax(0,1fr)]'>
      <aside className='hidden min-h-0 border-r bg-muted/20 lg:flex lg:flex-col'>
        <div className='flex items-center justify-between p-4'>
          <p className='text-sm font-semibold'>对话记录</p>
          <Button
            size='icon-sm'
            variant='ghost'
            aria-label='新建对话'
            onClick={resetChat}
          >
            <Icons.add />
          </Button>
        </div>
        <div className='px-3 pb-2'>
          <Button
            className='w-full justify-start'
            variant='outline'
            onClick={resetChat}
          >
            <Icons.edit /> 新建对话
          </Button>
        </div>
        <div className='min-h-0 flex-1 overflow-y-auto px-3 py-3'>
          {conversations.length > 0 ? (
            <div className='space-y-1'>
              {conversations.map((conversation) => {
                const isCurrent = conversation.conversation_id === conversationId;
                const isRunning =
                  isCurrent &&
                  Boolean(runId) &&
                  (run.connection === 'connecting' || run.connection === 'live');
                return (
                  <div
                    key={conversation.conversation_id}
                    className='group flex items-center gap-1'
                  >
                    <Button
                      variant={isCurrent ? 'secondary' : 'ghost'}
                      className='h-auto min-w-0 flex-1 justify-start px-3 py-2 text-left'
                      onClick={() =>
                        selectConversation(conversation.conversation_id)
                      }
                    >
                      <span className='line-clamp-2 text-sm'>
                        {conversation.preview}
                      </span>
                    </Button>
                    <Button
                      size='icon-xs'
                      variant='ghost'
                      aria-label='删除对话'
                      title={isRunning ? '任务运行中，暂不可删除' : '删除对话'}
                      disabled={isRunning || deletingConversationId === conversation.conversation_id}
                      onClick={() =>
                        void removeConversation(conversation.conversation_id)
                      }
                    >
                      {deletingConversationId === conversation.conversation_id ? (
                        <Icons.spinner className='animate-spin' />
                      ) : (
                        <Icons.trash />
                      )}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className='px-1 text-xs leading-relaxed text-muted-foreground'>
              暂无已保存对话。发送第一条消息后会显示在这里。
            </p>
          )}
        </div>
        <div className='border-t p-3'>
          <AdvertisingReportImport />
        </div>
      </aside>

      <section className='flex min-h-0 flex-col bg-background'>
        <div ref={messagesRef} className='min-h-0 flex-1 overflow-y-auto'>
          <div className='mx-auto flex min-h-full max-w-4xl flex-col gap-6 px-4 py-6 sm:px-6'>
            <Message>
              <MessageAvatar className='size-8 bg-primary text-primary-foreground'>
                <Icons.sparkles className='size-4' />
              </MessageAvatar>
              <MessageContent>
                <MessageHeader>Amazon Ops 总控</MessageHeader>
                <Bubble variant='muted' className='max-w-[92%]'>
                  <BubbleContent>
                    请输入基于已导入广告报表的查询问题。系统仅返回本地数据库的只读查询结果。
                  </BubbleContent>
                </Bubble>
              </MessageContent>
            </Message>

            {historyError && (
              <div className='rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive'>
                {historyError}
              </div>
            )}

            {history.map((message, index) =>
              message.kind === 'summary' ? (
                <div
                  key={`summary-${index}`}
                  className='rounded-lg border border-dashed bg-muted/30 p-3 text-sm text-muted-foreground'
                >
                  <p className='mb-1 font-medium text-foreground'>已压缩的历史对话</p>
                  <MarkdownContent content={message.content} />
                </div>
              ) : message.role === 'user' ? (
                <Message key={`${message.role}-${index}`} align='end'>
                  <MessageAvatar className='size-8 bg-secondary'>
                    <Icons.user className='size-4' />
                  </MessageAvatar>
                  <MessageContent>
                    <MessageHeader>你</MessageHeader>
                    <Bubble>
                      <BubbleContent>{message.content}</BubbleContent>
                    </Bubble>
                  </MessageContent>
                </Message>
              ) : (
                <Message key={`${message.role}-${index}`}>
                  <MessageAvatar className='size-8 bg-primary text-primary-foreground'>
                    <Icons.sparkles className='size-4' />
                  </MessageAvatar>
                  <MessageContent>
                    <MessageHeader>Amazon Ops 总控</MessageHeader>
                    <Bubble variant='outline' className='max-w-[92%]'>
                      <BubbleContent>
                        <MarkdownContent content={message.content} />
                      </BubbleContent>
                    </Bubble>
                  </MessageContent>
                </Message>
              )
            )}

            {question && (
              <Message align='end'>
                <MessageAvatar className='size-8 bg-secondary'>
                  <Icons.user className='size-4' />
                </MessageAvatar>
                <MessageContent>
                  <MessageHeader>你</MessageHeader>
                  <Bubble>
                    <BubbleContent className='space-y-2'>
                      {questionImages.length > 0 && (
                        <div className='flex flex-wrap gap-2'>
                          {questionImages.map((attachment) => (
                            <img
                              key={attachment.previewUrl}
                              src={attachment.previewUrl}
                              alt='已发送图片'
                              className='size-28 rounded-lg border object-cover'
                            />
                          ))}
                        </div>
                      )}
                      <p>{question}</p>
                    </BubbleContent>
                  </Bubble>
                </MessageContent>
              </Message>
            )}

            {submitting && (
              <p className='pl-10 text-sm text-muted-foreground'>
                正在向后端创建真实任务…
              </p>
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

        <form
          onSubmit={submitQuestion}
          className='border-t bg-background p-3 sm:p-4'
        >
          <div className='mx-auto max-w-4xl rounded-xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/30'>
            {imageAttachments.length > 0 && (
              <div className='flex flex-wrap gap-2 px-2 pt-2'>
                {imageAttachments.map((attachment) => (
                  <div key={attachment.previewUrl} className='group relative'>
                    <img
                      src={attachment.previewUrl}
                      alt='待发送图片'
                      className='size-20 rounded-lg border object-cover'
                    />
                    <Button
                      type='button'
                      size='icon-xs'
                      variant='secondary'
                      className='absolute -right-2 -top-2 rounded-full border opacity-0 shadow-sm transition-opacity group-hover:opacity-100 focus-visible:opacity-100'
                      onClick={() => removeImageAttachment(attachment.previewUrl)}
                      aria-label='移除图片'
                    >
                      <Icons.close />
                    </Button>
                  </div>
                ))}
              </div>
            )}
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onPaste={handlePaste}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false;
              }}
              onKeyDown={handleKeyDown}
              placeholder='输入需要查询、分析或解释的运营问题'
              aria-label='向运营助手提问'
              className='max-h-32 min-h-14 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0'
            />
            <div className='flex items-center justify-between gap-2 px-1 pt-1'>
              <div className='flex items-center gap-4'>
                <div className='flex items-center gap-2'>
                  <span className='text-sm text-muted-foreground'>模型</span>
                  <Select
                    value={model}
                    onValueChange={(value) => setModel(value as ModelName)}
                    disabled={submitting}
                  >
                    <SelectTrigger size='sm' aria-label='选择模型'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align='start'>
                      <SelectItem value='deepseek-v4-flash'>Flash（更快）</SelectItem>
                      <SelectItem value='deepseek-v4-pro'>Pro（更强）</SelectItem>
                      <SelectItem value='deepseek-v4-flash-vision-exp'>Vision（看图）</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className='flex items-center gap-2'>
                  <span className='text-sm text-muted-foreground'>思考程度</span>
                  <Select
                    value={reasoningEffort}
                    onValueChange={(value) => setReasoningEffort(value as ReasoningEffort)}
                    disabled={submitting}
                  >
                    <SelectTrigger size='sm' aria-label='推理等级'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align='start'>
                      <SelectItem value='off'>Off（不思考）</SelectItem>
                      <SelectItem value='low'>Low（低）</SelectItem>
                      <SelectItem value='high'>High（高）</SelectItem>
                      <SelectItem value='max'>Max（最高）</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <Button
                type='submit'
                size='icon'
                disabled={
                  (!input.trim() && imageAttachments.length === 0) ||
                  submitting ||
                  !conversationId
                }
                aria-label='发送问题'
              >
                {submitting ? (
                  <Icons.spinner className='animate-spin' />
                ) : (
                  <Icons.send />
                )}
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
