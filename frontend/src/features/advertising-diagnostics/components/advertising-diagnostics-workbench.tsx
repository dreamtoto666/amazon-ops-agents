"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Icons } from "@/components/icons";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  advertisingHistoryQueryOptions,
  advertisingSelectionDirectoryQueryOptions,
} from "../api/queries";
import {
  createAdvertisingRun,
  deleteAdvertisingHistory,
  getAdvertisingRun,
  subscribeToAdvertisingRun,
} from "../api/service";
import type {
  AdvertisingAnomaly,
  AdvertisingDiagnosticResult,
  AdvertisingRunRecord,
  AdvertisingStage,
  AdvertisingStageEvent,
} from "../api/types";
import { cn } from "@/lib/utils";

const agents: Array<{
  stage: AdvertisingStage;
  name: string;
  description: string;
  icon: typeof Icons.search;
}> = [
  {
    stage: "data_inspection",
    name: "数据巡检 Agent",
    description: "默认检查当前广告表现；启用趋势对比后识别周期变化异常。",
    icon: Icons.search,
  },
  {
    stage: "problem_attribution",
    name: "问题归因 Agent",
    description: "只针对异常活动下钻广告组、关键词、投放和搜索词证据。",
    icon: Icons.adjustments,
  },
  {
    stage: "strategy_recommendation",
    name: "策略建议 Agent",
    description: "根据归因、经营目标和风险约束形成运营建议。",
    icon: Icons.sparkles,
  },
  {
    stage: "review_todo",
    name: "复核与代办 Agent",
    description: "复核证据、冲突和重复项，生成待审批代办。",
    icon: Icons.forms,
  },
];

const anomalyLabels: Record<string, string> = {
  spend_spike: "花费突增",
  sales_drop: "销售额下降",
  orders_drop: "订单下降",
  acos_rise: "ACOS 上升",
  roas_drop: "ROAS 下降",
  cpc_rise: "CPC 上升",
  ctr_drop: "CTR 下降",
  cvr_drop: "CVR 下降",
  zero_order_waste: "高消耗无订单",
  budget_constrained: "预算受限",
  delivery_drop: "投放量下降",
};

const actionLabels: Record<string, string> = {
  observe: "观察并复查",
  adjust_budget: "调整预算",
  adjust_bid: "调整竞价",
  pause_entity: "暂停广告对象",
  add_negative: "添加否定投放",
  move_search_term: "迁移搜索词",
  optimize_listing: "检查 Listing",
  check_inventory: "检查库存",
};

const priorityLabels = {
  urgent: "紧急",
  high: "高",
  medium: "中",
  low: "低",
};

function ScopeField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function formatDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function daysAgo(days: number) {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() - days);
  return formatDate(date);
}

interface DateRangeFieldProps {
  label: string;
  startDate: string;
  endDate: string;
  onStartDateChange: (value: string) => void;
  onEndDateChange: (value: string) => void;
  disabled?: boolean;
}

function DateRangeField({
  label,
  startDate,
  endDate,
  onStartDateChange,
  onEndDateChange,
  disabled,
}: DateRangeFieldProps) {
  return (
    <ScopeField label={label}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
        <Input
          type="date"
          aria-label={`${label}开始日期`}
          value={startDate}
          max={endDate}
          disabled={disabled}
          onChange={(event) => onStartDateChange(event.target.value)}
        />
        <span className="text-xs text-muted-foreground">至</span>
        <Input
          type="date"
          aria-label={`${label}结束日期`}
          value={endDate}
          min={startDate}
          disabled={disabled}
          onChange={(event) => onEndDateChange(event.target.value)}
        />
      </div>
    </ScopeField>
  );
}

function stageStatus(
  stage: AdvertisingStage,
  events: AdvertisingStageEvent[],
  runCompleted: boolean,
) {
  const relevant = events.filter((event) => event.stage === stage);
  if (relevant.some((event) => event.event === "stage.failed")) return "failed";
  if (relevant.some((event) => event.event === "stage.completed"))
    return "completed";
  if (relevant.some((event) => event.event === "stage.started"))
    return "active";
  if (runCompleted) return "skipped";
  return "pending";
}

function StageStatus({ status }: { status: ReturnType<typeof stageStatus> }) {
  if (status === "completed") {
    return (
      <span className="flex items-center gap-1 text-xs text-emerald-600">
        <Icons.circleCheck className="size-3.5" /> 已完成
      </span>
    );
  }
  if (status === "active") {
    return (
      <span className="flex items-center gap-1 text-xs text-primary">
        <Icons.spinner className="size-3.5 animate-spin" /> 运行中
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="flex items-center gap-1 text-xs text-destructive">
        <Icons.warning className="size-3.5" /> 失败
      </span>
    );
  }
  return (
    <span className="text-xs text-muted-foreground">
      {status === "skipped" ? "本次未触发" : "等待运行"}
    </span>
  );
}

function severityBadge(severity: AdvertisingAnomaly["severity"]) {
  const label = { critical: "严重", high: "高", medium: "中", low: "低" }[
    severity
  ];
  return (
    <Badge variant={severity === "critical" ? "destructive" : "outline"}>
      {label}
    </Badge>
  );
}

function formatMetric(metric: string, value: number | null) {
  if (value === null) return "—";
  if (["acos", "ctr", "cvr"].includes(metric))
    return `${(value * 100).toFixed(1)}%`;
  if (["spend", "sales", "cpc"].includes(metric)) return value.toFixed(2);
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function proposedChangeLabel(value: Record<string, unknown>) {
  if (typeof value.bid_change_percent === "number")
    return `竞价 ${value.bid_change_percent}%`;
  if (typeof value.budget_change_percent === "number") {
    return `预算 +${value.budget_change_percent}%`;
  }
  if (typeof value.review_after_days === "number")
    return `${value.review_after_days} 天后复查`;
  return "人工复核后决定";
}

function isInternalTrustBoundaryWarning(warning: string) {
  return (
    (warning.includes("领星返回") && warning.includes("不可信")) ||
    (warning.includes("广告名称") && warning.includes("不可信")) ||
    (warning.includes("外部文本") && warning.includes("不可信"))
  );
}

function visibleDiagnosticWarnings(warnings: string[] | undefined) {
  const visible = new Set<string>();
  for (const warning of warnings ?? []) {
    if (isInternalTrustBoundaryWarning(warning)) continue;
    if (warning.includes("无基准期") || warning.includes("未启用基准周期")) {
      visible.add("当前周期体检模式：未启用基准周期趋势对比。");
      continue;
    }
    visible.add(warning);
  }
  return [...visible];
}

function historyRunTitle(record: AdvertisingRunRecord) {
  const scope = record.display_scope;
  if (!scope?.shop_label) return record.result?.summary ?? record.error?.message ?? "广告巡检进行中";
  const period = scope.current_period;
  const range = period ? `${period.start} 至 ${period.end}` : "当前周期";
  return `${scope.shop_label} · ${range} 广告巡检`;
}

function historyRunDetail(record: AdvertisingRunRecord) {
  if (!record.result) return record.error?.message ?? "巡检进行中";
  return `发现 ${record.result.anomalies.length} 个异常，生成 ${record.result.todos.length} 个运营代办。`;
}

export function AdvertisingDiagnosticsWorkbench() {
  const directoryQuery = useQuery(advertisingSelectionDirectoryQueryOptions());
  const historyQuery = useQuery(advertisingHistoryQueryOptions());
  const [responsibleRef, setResponsibleRef] = useState("");
  const [shopRef, setShopRef] = useState("");
  const [productRef, setProductRef] = useState("");
  const [currentStart, setCurrentStart] = useState(() => daysAgo(6));
  const [currentEnd, setCurrentEnd] = useState(() => daysAgo(0));
  const [baselineStart, setBaselineStart] = useState(() => daysAgo(13));
  const [baselineEnd, setBaselineEnd] = useState(() => daysAgo(7));
  const [compareBaseline, setCompareBaseline] = useState(false);
  const [goal, setGoal] = useState<"profit" | "balanced" | "scale">("balanced");
  const [runId, setRunId] = useState<string>();
  const [traceId, setTraceId] = useState<string>();
  const [events, setEvents] = useState<AdvertisingStageEvent[]>([]);
  const [result, setResult] = useState<AdvertisingDiagnosticResult>();
  const [historyScope, setHistoryScope] = useState<{ shop_label: string; campaign_count: number } | null>();
  const [expandedTodoId, setExpandedTodoId] = useState<string | null>(null);
  const [runError, setRunError] = useState<string>();
  const [connected, setConnected] = useState(false);

  const responsibles = useMemo(() => {
    const unique = new Map<string, { responsible_ref: string; label: string }>();
    directoryQuery.data?.stores.forEach((store) => store.responsibles.forEach((person) => unique.set(person.responsible_ref, person)));
    return [...unique.values()];
  }, [directoryQuery.data]);
  const stores = useMemo(() => directoryQuery.data?.stores.filter((store) => store.responsibles.some((person) => person.responsible_ref === responsibleRef)) ?? [], [directoryQuery.data, responsibleRef]);
  const selectedStore = stores.find((store) => store.shop_ref === shopRef);
  const products = selectedStore?.responsibles.find((person) => person.responsible_ref === responsibleRef)?.products ?? [];
  useEffect(() => { if (!responsibles.some((item) => item.responsible_ref === responsibleRef)) { setResponsibleRef(""); setShopRef(""); setProductRef(""); } }, [responsibles, responsibleRef]);
  useEffect(() => { if (!stores.some((item) => item.shop_ref === shopRef)) { setShopRef(""); setProductRef(""); } }, [stores, shopRef]);
  useEffect(() => { if (!products.some((item) => item.product_ref === productRef)) setProductRef(""); }, [products, productRef]);

  useEffect(() => {
    if (!runId) return;
    return subscribeToAdvertisingRun(runId, {
      onOpen: () => setConnected(true),
      onEvent: (event) => {
        setEvents((current) =>
          current.some((item) => item.event_id === event.event_id)
            ? current
            : [...current, event],
        );
        if (event.event === "run.completed") {
          setConnected(false);
          const completedResult = event.data.result as
            AdvertisingDiagnosticResult | undefined;
          if (completedResult) setResult(completedResult);
          else {
            void getAdvertisingRun(runId).then((record) => {
              if (record.result) setResult(record.result);
            });
          }
          void historyQuery.refetch();
        }
        if (event.event === "run.failed") {
          setConnected(false);
          setRunError(
            typeof event.data.error === "string"
              ? event.data.error
              : "广告巡检运行失败。",
          );
          void historyQuery.refetch();
        }
      },
      onError: (error) => setRunError(error.message),
    });
  }, [runId]);

  const createRun = useMutation({
    mutationFn: (input: Parameters<typeof createAdvertisingRun>[0]) =>
      createAdvertisingRun(input),
    onSuccess: (created) => {
      setRunId(created.run_id);
      setTraceId(created.trace_id);
      setEvents([]);
      setResult(undefined);
      setHistoryScope(undefined);
      setRunError(undefined);
      setConnected(false);
      void historyQuery.refetch();
    },
    onError: (error) => {
      setRunError(
        error instanceof Error ? error.message : "无法启动广告巡检。",
      );
    },
  });

  const deleteHistory = useMutation({
    mutationFn: deleteAdvertisingHistory,
    onSuccess: (_, deletedRunId) => {
      if (runId === deletedRunId) {
        setRunId(undefined);
        setTraceId(undefined);
        setEvents([]);
        setResult(undefined);
        setRunError(undefined);
      }
      void historyQuery.refetch();
    },
  });

  const validationError = useMemo(() => {
    if (currentStart > currentEnd)
      return "当前周期的结束日期不能早于开始日期。";
    if (compareBaseline && baselineStart > baselineEnd)
      return "基准周期的结束日期不能早于开始日期。";
    return undefined;
  }, [baselineEnd, baselineStart, compareBaseline, currentEnd, currentStart]);

  const isRunning =
    createRun.isPending || Boolean(runId && !result && !runError);
  const runCompleted = Boolean(result);
  const visibleWarnings = visibleDiagnosticWarnings(result?.warnings);
  const highPriorityCount =
    result?.anomalies.filter((item) =>
      ["high", "critical"].includes(item.severity),
    ).length ?? 0;
  const overviewCards = [
    {
      label: "发现异常",
      value: result ? result.anomalies.length : "—",
      hint: result ? result.summary : "等待首次巡检",
      icon: Icons.warning,
    },
    {
      label: "高优先级",
      value: result ? highPriorityCount : "—",
      hint: result ? "严重和高等级异常" : "等待首次巡检",
      icon: Icons.trendingUp,
    },
    {
      label: "待处理代办",
      value: result ? result.todos.length : "—",
      hint: result ? "全部需要人工审批" : "等待首次巡检",
      icon: Icons.forms,
    },
    {
      label: "已覆盖店铺",
      value: historyScope?.shop_label || selectedStore ? 1 : "—",
      hint: historyScope?.shop_label ?? selectedStore?.label ?? "选择巡检范围",
      icon: Icons.product,
    },
  ];

  function startRun() {
    if (!shopRef || !productRef || !directoryQuery.data || validationError) return;
    // A new submission begins a distinct diagnostic view. Do not leave a
    // previous run's failure banner visible while this request is starting.
    setRunError(undefined);
    setResult(undefined);
    setEvents([]);
    setRunId(undefined);
    setTraceId(undefined);
    setHistoryScope(undefined);
    setConnected(false);
    createRun.mutate({
      selection_version: directoryQuery.data.version,
      shop_ref: shopRef,
      product_refs: [productRef],
      current_period: { start: currentStart, end: currentEnd },
      ...(compareBaseline
        ? { baseline_period: { start: baselineStart, end: baselineEnd } }
        : {}),
      goal: { growth_priority: goal },
      trigger: "manual",
    });
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="border-b">
          <CardTitle>巡检范围</CardTitle>
          <CardDescription>
            默认只检查当前周期；需要识别趋势变化时可启用基准周期对比。
          </CardDescription>
          <CardAction>
            <Badge variant={directoryQuery.isError ? "destructive" : "outline"}>
              {directoryQuery.isLoading
                ? "正在连接领星"
                : directoryQuery.isError
                  ? "领星连接失败"
                  : `已授权选择目录 · ${directoryQuery.data?.stores.length ?? 0} 店铺`}
            </Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <ScopeField label="负责人">
              <NativeSelect
                className="w-full"
                value={responsibleRef}
                disabled={directoryQuery.isLoading || isRunning}
                onChange={(event) => setResponsibleRef(event.target.value)}
              >
                <NativeSelectOption value="" disabled>
                  {directoryQuery.isLoading ? "正在加载选择目录" : "选择负责人"}
                </NativeSelectOption>
                {responsibles.map((person) => (
                  <NativeSelectOption key={person.responsible_ref} value={person.responsible_ref}>{person.label}</NativeSelectOption>
                ))}
              </NativeSelect>
            </ScopeField>
            <ScopeField label="广告店铺">
              <NativeSelect className="w-full" value={shopRef} disabled={!responsibleRef || isRunning} onChange={(event) => setShopRef(event.target.value)}>
                <NativeSelectOption value="" disabled>选择负责人名下店铺</NativeSelectOption>
                {stores.map((store) => <NativeSelectOption key={store.shop_ref} value={store.shop_ref}>{store.label}</NativeSelectOption>)}
              </NativeSelect>
            </ScopeField>
            <ScopeField label="父 ASIN">
              <NativeSelect className="w-full" value={productRef} disabled={!shopRef || isRunning} onChange={(event) => setProductRef(event.target.value)}>
                <NativeSelectOption value="" disabled>选择产品</NativeSelectOption>
                {products.map((product) => <NativeSelectOption key={product.product_ref} value={product.product_ref}>{product.parent_asin}</NativeSelectOption>)}
              </NativeSelect>
            </ScopeField>
            <DateRangeField
              label="当前周期"
              startDate={currentStart}
              endDate={currentEnd}
              onStartDateChange={setCurrentStart}
              onEndDateChange={setCurrentEnd}
              disabled={isRunning}
            />
            <ScopeField label="趋势对比">
              <label className="flex h-9 items-center gap-2 rounded-md border px-3 text-sm">
                <input
                  type="checkbox"
                  checked={compareBaseline}
                  disabled={isRunning}
                  onChange={(event) => setCompareBaseline(event.target.checked)}
                />
                启用基准周期对比（可选）
              </label>
            </ScopeField>
            <ScopeField label="经营目标">
              <NativeSelect
                className="w-full"
                value={goal}
                disabled={isRunning}
                onChange={(event) =>
                  setGoal(event.target.value as "profit" | "balanced" | "scale")
                }
              >
                <NativeSelectOption value="profit">利润优先</NativeSelectOption>
                <NativeSelectOption value="balanced">
                  平衡增长
                </NativeSelectOption>
                <NativeSelectOption value="scale">放量优先</NativeSelectOption>
              </NativeSelect>
            </ScopeField>
            <div className="flex items-end">
              <Button
                className="w-full"
                disabled={
                  !responsibleRef || !shopRef || !productRef ||
                  Boolean(validationError) ||
                  directoryQuery.isError ||
                  isRunning
                }
                onClick={startRun}
              >
                {isRunning ? (
                  <Icons.spinner className="animate-spin" />
                ) : (
                  <Icons.sparkles />
                )}
                {isRunning ? "巡检中" : "开始巡检"}
              </Button>
            </div>
          </div>
          {compareBaseline && (
            <div className="mt-4 max-w-xl">
              <DateRangeField
                label="基准周期"
                startDate={baselineStart}
                endDate={baselineEnd}
                onStartDateChange={setBaselineStart}
                onEndDateChange={setBaselineEnd}
                disabled={isRunning}
              />
            </div>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">只读模式</Badge>
            <Badge variant="outline">DeepSeek 归因与策略</Badge>
            <span>
              当前周期默认查询领星：ACOS 与 CTR 使用经营目标阈值巡检；CPC 仅展示。所有建议只生成代办，不自动修改广告。
            </span>
            {runId && (
              <span className="font-mono">任务：{runId.slice(0, 18)}…</span>
            )}
            {traceId && (
              <span className="font-mono">链路：{traceId.slice(0, 18)}…</span>
            )}
            {events.at(-1)?.stage && <span>阶段：{events.at(-1)?.stage}</span>}
            {connected && <span className="text-primary">阶段事件已连接</span>}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle>历史巡检</CardTitle>
          <CardDescription>最近 12 次广告诊断记录，点击可恢复查看结果。</CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" onClick={() => void historyQuery.refetch()}>
              刷新记录
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          {historyQuery.isLoading ? (
            <p className="py-4 text-sm text-muted-foreground">正在加载历史巡检记录…</p>
          ) : historyQuery.error ? (
            <p className="py-4 text-sm text-destructive">
              历史记录加载失败：{historyQuery.error instanceof Error ? historyQuery.error.message : "请稍后刷新重试。"}
            </p>
          ) : historyQuery.data?.length ? (
            <div className="divide-y rounded-xl border">
              {historyQuery.data.map((record) => (
                <div key={record.run_id} className="flex items-center gap-2 px-2">
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center justify-between gap-4 px-2 py-3 text-left transition-colors hover:bg-muted/50"
                    onClick={() => {
                      setRunId(record.run_id);
                      setTraceId(record.trace_id);
                      setEvents([]);
                      setRunError(record.error?.message);
                      setResult(record.result ?? undefined);
                      setHistoryScope(record.display_scope ?? null);
                    }}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {historyRunTitle(record)}
                      </p>
                      <p className="mt-1 font-mono text-xs text-muted-foreground">
                        {historyRunDetail(record)}
                        <span className="px-1">·</span>
                        {record.run_id.slice(0, 24)}…
                        {record.created_at
                          ? ` · ${new Date(record.created_at).toLocaleString("zh-CN")}`
                          : ""}
                      </p>
                    </div>
                    <Badge variant={record.status === "failed" ? "destructive" : "outline"}>
                      {record.status === "completed" ? "已完成" : record.status === "failed" ? "失败" : "进行中"}
                    </Badge>
                  </button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={record.status === "running" || deleteHistory.isPending}
                    aria-label="删除历史记录"
                    title={record.status === "running" ? "巡检进行中，暂不能删除" : "删除历史记录"}
                    onClick={() => {
                      if (window.confirm("确定删除这条历史巡检记录吗？此操作无法撤销。"))
                        deleteHistory.mutate(record.run_id);
                    }}
                  >
                    <Icons.trash className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-4 text-sm text-muted-foreground">尚无历史巡检记录。</p>
          )}
          {deleteHistory.error && (
            <p className="mt-3 text-sm text-destructive">
              删除历史记录失败：{deleteHistory.error instanceof Error ? deleteHistory.error.message : "请稍后重试。"}
            </p>
          )}
        </CardContent>
      </Card>

      {(validationError || runError || directoryQuery.error) && (
        <Alert variant="destructive">
          <Icons.warning />
          <AlertTitle>暂时无法运行巡检</AlertTitle>
          <AlertDescription>
            {validationError ??
              runError ??
              (directoryQuery.error instanceof Error
                ? directoryQuery.error.message
                : "无法加载领星店铺。")}
          </AlertDescription>
        </Alert>
      )}

      {visibleWarnings.map((warning, index) => (
        <Alert key={`warning-${index}-${warning}`}>
          <Icons.info />
          <AlertTitle>巡检提示</AlertTitle>
          <AlertDescription>{warning}</AlertDescription>
        </Alert>
      ))}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {overviewCards.map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.label} size="sm">
              <CardContent className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm text-muted-foreground">{item.label}</p>
                  <p className="mt-2 text-2xl font-semibold">{item.value}</p>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {item.hint}
                  </p>
                </div>
                <div className="rounded-lg bg-muted p-2 text-muted-foreground">
                  <Icon className="size-4" />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>四 Agent 诊断流程</CardTitle>
          <CardDescription>
            阶段事件实时更新；无异常时会跳过归因和策略阶段。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 lg:grid-cols-4">
            {agents.map((agent, index) => {
              const Icon = agent.icon;
              const status = stageStatus(agent.stage, events, runCompleted);
              return (
                <div
                  key={agent.name}
                  className={cn(
                    "relative rounded-xl border bg-muted/20 p-4",
                    status === "active" && "border-primary/40 bg-primary/5",
                    index < agents.length - 1 &&
                      "after:absolute after:top-1/2 after:-right-3 after:hidden after:h-px after:w-3 after:bg-border lg:after:block",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <div className="flex size-8 items-center justify-center rounded-lg bg-background ring-1 ring-foreground/10">
                      <Icon className="size-4" />
                    </div>
                    <div>
                      <p className="font-medium">{agent.name}</p>
                      <StageStatus status={status} />
                    </div>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                    {agent.description}
                  </p>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle>诊断结果</CardTitle>
          <CardDescription>
            {result?.summary ??
              "异常、运营代办和原始证据将按同一个巡检任务关联。"}
          </CardDescription>
          <CardAction>
            <Badge variant="outline">所有写操作均需审批</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="anomalies">
            <TabsList variant="line">
              <TabsTrigger value="anomalies">
                异常列表 {result ? `(${result.anomalies.length})` : ""}
              </TabsTrigger>
              <TabsTrigger value="todos">
                运营代办 {result ? `(${result.todos.length})` : ""}
              </TabsTrigger>
              <TabsTrigger value="evidence">
                证据记录 {result ? `(${result.evidence.length})` : ""}
              </TabsTrigger>
            </TabsList>
            <Separator />
            <TabsContent value="anomalies" className="pt-4">
              {result?.anomalies.length ? (
                <div className="overflow-hidden rounded-xl border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>等级</TableHead>
                        <TableHead>异常</TableHead>
                        <TableHead>广告活动</TableHead>
                        <TableHead>当前周期</TableHead>
                        <TableHead>基准周期</TableHead>
                        <TableHead>置信度</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {result.anomalies.map((anomaly) => (
                        <TableRow key={anomaly.anomaly_id}>
                          <TableCell>
                            {severityBadge(anomaly.severity)}
                          </TableCell>
                          <TableCell className="font-medium">
                            {anomalyLabels[anomaly.anomaly_type] ??
                              anomaly.anomaly_type}
                          </TableCell>
                          <TableCell>
                            {anomaly.entity.name ?? anomaly.entity.entity_id}
                          </TableCell>
                          <TableCell>
                            {formatMetric(
                              anomaly.metric,
                              anomaly.current_value,
                            )}
                          </TableCell>
                          <TableCell>
                            {formatMetric(
                              anomaly.metric,
                              anomaly.baseline_value,
                            )}
                          </TableCell>
                          <TableCell>
                            {Math.round(anomaly.confidence * 100)}%
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <Empty className="min-h-52 border">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      {result ? <Icons.circleCheck /> : <Icons.warning />}
                    </EmptyMedia>
                    <EmptyTitle>
                      {result ? "本次未发现显著异常" : "尚无异常记录"}
                    </EmptyTitle>
                    <EmptyDescription>
                      {result
                        ? "当前周期与基准周期的变化未达到巡检阈值。"
                        : "完成首次巡检后，这里会按严重程度展示异常。"}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </TabsContent>
            <TabsContent value="todos" className="pt-4">
              <div className="overflow-hidden rounded-xl border">
                <Table className="min-w-[66rem] table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-20">优先级</TableHead>
                      <TableHead className="w-[38%]">运营代办</TableHead>
                      <TableHead className="w-[22%]">目标对象</TableHead>
                      <TableHead className="w-[16%]">建议动作</TableHead>
                      <TableHead className="w-16">证据</TableHead>
                      <TableHead className="w-20">状态</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result?.todos.length ? (
                      result.todos.map((todo) => {
                        const expanded = expandedTodoId === todo.todo_id;
                        return (
                        <Fragment key={todo.todo_id}>
                        <TableRow>
                          <TableCell className="align-top">
                            <Badge
                              variant={
                                todo.priority === "urgent"
                                  ? "destructive"
                                  : "outline"
                              }
                            >
                              {priorityLabels[todo.priority]}
                            </Badge>
                          </TableCell>
                          <TableCell className="align-top whitespace-normal">
                            <p className="truncate font-medium">{todo.title}</p>
                            <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                              {todo.description}
                            </p>
                            {todo.detail && (
                              <Button
                                className="mt-3"
                                variant="outline"
                                size="xs"
                                onClick={() => setExpandedTodoId(expanded ? null : todo.todo_id)}
                              >
                                {expanded ? "收起诊断详情" : "查看诊断详情"}
                                <Icons.chevronDown className={cn("transition-transform", expanded && "rotate-180")} />
                              </Button>
                            )}
                          </TableCell>
                          <TableCell className="align-top">
                            <Tooltip>
                              <TooltipTrigger className="block w-full truncate text-left">
                                {todo.target.name ?? todo.target.entity_id}
                              </TooltipTrigger>
                              <TooltipContent className="max-w-md break-words whitespace-normal">
                                {todo.target.name ?? todo.target.entity_id}
                              </TooltipContent>
                            </Tooltip>
                          </TableCell>
                          <TableCell className="align-top whitespace-normal">
                            <p className="break-words">
                              {actionLabels[todo.action_type] ??
                                todo.action_type}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {proposedChangeLabel(todo.proposed_change)}
                            </p>
                          </TableCell>
                          <TableCell className="align-top">{todo.evidence_refs.length} 条</TableCell>
                          <TableCell className="align-top">
                            <Badge variant="secondary">待审批</Badge>
                          </TableCell>
                        </TableRow>
                        {expanded && todo.detail && (
                          <TableRow className="bg-muted/20 hover:bg-muted/20">
                            <TableCell colSpan={6} className="px-6 pb-5 pt-1">
                              <p className="max-w-6xl break-words whitespace-pre-line text-sm leading-7 text-muted-foreground">
                                {todoDetailNarrative(todo)}
                              </p>
                            </TableCell>
                          </TableRow>
                        )}
                        </Fragment>
                        );
                      })
                    ) : (
                      <TableRow>
                        <TableCell colSpan={6} className="h-52">
                          <Empty>
                            <EmptyHeader>
                              <EmptyMedia variant="icon">
                                <Icons.forms />
                              </EmptyMedia>
                              <EmptyTitle>尚未生成运营代办</EmptyTitle>
                              <EmptyDescription>
                                复核 Agent
                                只会把有证据、无冲突的建议转换为代办。
                              </EmptyDescription>
                            </EmptyHeader>
                          </Empty>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </TabsContent>
            <TabsContent value="evidence" className="pt-4">
              {result?.evidence.length ? (
                <div className="overflow-hidden rounded-xl border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>领星工具</TableHead>
                        <TableHead>数据角色</TableHead>
                        <TableHead>记录数</TableHead>
                        <TableHead>获取时间</TableHead>
                        <TableHead>证据编号</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {result.evidence.map((evidence) => (
                        <TableRow key={evidence.evidence_id}>
                          <TableCell className="font-mono text-xs">
                            {evidence.tool}
                          </TableCell>
                          <TableCell>
                            {evidence.query.period_role === "baseline"
                              ? "基准周期"
                              : "当前周期"}
                          </TableCell>
                          <TableCell>{evidence.record_count}</TableCell>
                          <TableCell>
                            {new Date(evidence.fetched_at).toLocaleString(
                              "zh-CN",
                            )}
                          </TableCell>
                          <TableCell className="font-mono text-xs">
                            {evidence.evidence_id}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <Empty className="min-h-52 border">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <Icons.search />
                    </EmptyMedia>
                    <EmptyTitle>尚无数据证据</EmptyTitle>
                    <EmptyDescription>
                      巡检后可查看领星工具、查询周期、记录数和证据引用。
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}

function todoDetailNarrative(
  todo: NonNullable<AdvertisingDiagnosticResult>["todos"][number],
) {
  const detail = todo.detail;
  if (!detail) return todo.description;
  return [
    `异常情况：${detail.diagnosis}`,
    `指标表现：${detail.metric_summary}`,
    `归因判断：${detail.attribution}`,
    `本次已下钻核查：${detail.checked_sources?.length ? detail.checked_sources.join("、") : "未取得可验证的下钻结果"}。`,
    `建议方案：${detail.recommendation}`,
    `预期影响：${detail.expected_effect ?? "暂无可量化的预期影响。"}`,
  ]
    .join("\n");
}
