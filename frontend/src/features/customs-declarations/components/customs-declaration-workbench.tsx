'use client';

import { useState, type ChangeEvent } from 'react';
import { Icons } from '@/components/icons';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  CustomsGenerationError,
  generateCustomsDeclarations,
  previewCustomsDeclarations
} from '../api/service';
import type {
  CustomsCategoryPreview,
  CustomsIssue,
  CustomsPreview
} from '../api/types';

const MAX_FILE_SIZE = 20 * 1024 * 1024;

interface UploadFieldProps {
  id: string;
  label: string;
  description: string;
  file: File | null;
  disabled: boolean;
  onChange: (file: File | null) => void;
}

function UploadField({
  id,
  label,
  description,
  file,
  disabled,
  onChange
}: UploadFieldProps) {
  function handleChange(event: ChangeEvent<HTMLInputElement>): void {
    onChange(event.target.files?.[0] ?? null);
  }

  return (
    <div className='space-y-2 rounded-xl border bg-muted/20 p-4'>
      <div className='flex items-start gap-3'>
        <div className='rounded-lg border bg-background p-2'>
          <Icons.fileTypeXls className='size-5 text-emerald-600' />
        </div>
        <div className='min-w-0 flex-1'>
          <Label htmlFor={id} className='text-base'>
            {label}
          </Label>
          <p className='mt-1 text-sm text-muted-foreground'>{description}</p>
        </div>
      </div>
      <Input
        id={id}
        type='file'
        accept='.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        disabled={disabled}
        onChange={handleChange}
      />
      <p className='truncate text-xs text-muted-foreground'>
        {file ? `已选择：${file.name}` : '仅支持 .xlsx，单文件不超过 20MB'}
      </p>
    </div>
  );
}

function formatDecimal(value: string, digits = 2): string {
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

function CategoryCard({ category }: { category: CustomsCategoryPreview }) {
  return (
    <Card className={category.generates_file ? '' : 'opacity-70'}>
      <CardHeader className='flex flex-row items-start justify-between space-y-0 pb-3'>
        <div>
          <CardTitle className='text-base'>
            {category.shipping_speed} × {category.trade_mode}
          </CardTitle>
          <p className='mt-1 text-xs text-muted-foreground'>
            {category.order_count} 个客户原单号 · {category.shipment_count} 个货件
          </p>
        </div>
        <Badge variant={category.generates_file ? 'default' : 'secondary'}>
          {category.generates_file ? '将生成' : '空分类'}
        </Badge>
      </CardHeader>
      <CardContent className='space-y-4'>
        <div className='grid grid-cols-2 gap-3 text-sm'>
          <Metric label='标准商品' value={`${category.item_count} 个`} />
          <Metric label='箱数' value={`${category.box_count} 箱`} />
          <Metric label='净重' value={`${formatDecimal(category.net_weight)} kg`} />
          <Metric label='毛重' value={`${formatDecimal(category.gross_weight)} kg`} />
          <Metric label='申报金额' value={`$${formatDecimal(category.amount)}`} wide />
        </div>
        {category.items.length > 0 ? (
          <div className='max-h-52 space-y-2 overflow-y-auto border-t pt-3'>
            {category.items.map((item) => (
              <div key={item.rule_id} className='flex items-start justify-between gap-3 text-xs'>
                <div className='min-w-0'>
                  <p className='truncate font-medium'>{item.declaration_name}</p>
                  <p className='truncate text-muted-foreground'>
                    {item.model} · {item.material} · HS {item.hs_code}
                  </p>
                </div>
                <div className='shrink-0 text-right'>
                  <p>{formatDecimal(item.quantity, 0)} 套</p>
                  <p className='text-muted-foreground'>${formatDecimal(item.amount)}</p>
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
  wide = false
}: {
  label: string;
  value: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? 'col-span-2' : ''}>
      <p className='text-xs text-muted-foreground'>{label}</p>
      <p className='font-medium tabular-nums'>{value}</p>
    </div>
  );
}

function IssueList({ title, issues, warning = false }: { title: string; issues: CustomsIssue[]; warning?: boolean }) {
  if (issues.length === 0) return null;
  return (
    <Alert variant={warning ? 'default' : 'destructive'}>
      {warning ? <Icons.warning className='size-4' /> : <Icons.alertCircle className='size-4' />}
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <ul className='mt-2 space-y-1'>
          {issues.map((issue, index) => (
            <li key={`${issue.code}-${issue.row ?? 0}-${index}`}>
              {issue.sheet ? `${issue.sheet}` : ''}
              {issue.row ? ` 第 ${issue.row} 行：` : issue.sheet ? '：' : ''}
              {issue.message}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

export function CustomsDeclarationWorkbench() {
  const [shipmentFile, setShipmentFile] = useState<File | null>(null);
  const [fbaFile, setFbaFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CustomsPreview | null>(null);
  const [busy, setBusy] = useState<'preview' | 'generate' | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function selectShipment(file: File | null): void {
    setShipmentFile(file);
    setPreview(null);
    setMessage(null);
  }

  function selectFba(file: File | null): void {
    setFbaFile(file);
    setPreview(null);
    setMessage(null);
  }

  function validateFiles(): string | null {
    if (!shipmentFile || !fbaFile) return '请先选择一八发货模板和 FBA 货件表。';
    if (!shipmentFile.name.toLowerCase().endsWith('.xlsx') || !fbaFile.name.toLowerCase().endsWith('.xlsx')) {
      return '两个文件都必须是 .xlsx 格式。';
    }
    if (shipmentFile.size > MAX_FILE_SIZE || fbaFile.size > MAX_FILE_SIZE) {
      return '单个文件不能超过 20MB。';
    }
    return null;
  }

  async function handlePreview(): Promise<void> {
    const error = validateFiles();
    if (error || !shipmentFile || !fbaFile) {
      setMessage(error);
      return;
    }
    setBusy('preview');
    setMessage(null);
    try {
      setPreview(await previewCustomsDeclarations(shipmentFile, fbaFile));
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : '分析失败，请稍后重试。');
    } finally {
      setBusy(null);
    }
  }

  async function handleGenerate(): Promise<void> {
    if (!shipmentFile || !fbaFile || !preview?.can_generate) return;
    setBusy('generate');
    setMessage(null);
    try {
      await generateCustomsDeclarations(shipmentFile, fbaFile);
      setMessage('报关单 ZIP 已生成并开始下载。');
    } catch (caught) {
      if (caught instanceof CustomsGenerationError) setPreview(caught.preview);
      setMessage(caught instanceof Error ? caught.message : '生成失败，请稍后重试。');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className='space-y-6'>
      <div className='grid gap-3 rounded-xl border bg-muted/20 p-4 text-sm md:grid-cols-3'>
        <div className='flex items-center gap-2'>
          <Icons.calendar className='size-4 text-muted-foreground' />
          日期自动使用上海时区今天
        </div>
        <div className='flex items-center gap-2'>
          <Icons.edit className='size-4 text-muted-foreground' />
          运费下载后手工填写
        </div>
        <div className='flex items-center gap-2'>
          <Icons.fileZip className='size-4 text-muted-foreground' />
          空分类不会生成文件
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>上传本次货件</CardTitle>
        </CardHeader>
        <CardContent className='space-y-5'>
          <div className='grid gap-4 md:grid-cols-2'>
            <UploadField
              id='shipment-file'
              label='一八发货模板'
              description='读取“美国专线箱单”中的客户原单号、渠道和贸易方式。'
              file={shipmentFile}
              disabled={busy !== null}
              onChange={selectShipment}
            />
            <UploadField
              id='fba-file'
              label='FBA 货件表'
              description='读取“装箱明细”中的品名、SKU、申报量、箱数和重量。'
              file={fbaFile}
              disabled={busy !== null}
              onChange={selectFba}
            />
          </div>
          <div className='flex flex-wrap items-center gap-3'>
            <Button type='button' onClick={() => void handlePreview()} disabled={busy !== null}>
              {busy === 'preview' ? <Icons.spinner className='mr-2 size-4 animate-spin' /> : <Icons.search className='mr-2 size-4' />}
              分析文件
            </Button>
            {preview ? (
              <span className='text-sm text-muted-foreground'>
                报关日期：{preview.declaration_date}
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {preview ? (
        <>
          <div className='grid gap-4 xl:grid-cols-2'>
            {preview.categories.map((category) => (
              <CategoryCard key={`${category.shipping_speed}-${category.trade_mode}`} category={category} />
            ))}
          </div>
          <IssueList title={`有 ${preview.errors.length} 项问题需要处理`} issues={preview.errors} />
          <IssueList title={`有 ${preview.warnings.length} 项提示`} issues={preview.warnings} warning />
          <div className='flex items-center gap-3'>
            <Button
              type='button'
              size='lg'
              disabled={!preview.can_generate || busy !== null}
              onClick={() => void handleGenerate()}
            >
              {busy === 'generate' ? <Icons.spinner className='mr-2 size-4 animate-spin' /> : <Icons.fileZip className='mr-2 size-4' />}
              生成并下载 ZIP
            </Button>
            {!preview.can_generate ? (
              <span className='text-sm text-muted-foreground'>修复全部阻断问题后才能生成。</span>
            ) : null}
          </div>
        </>
      ) : null}

      {message ? (
        <p className={message.includes('已生成') ? 'text-sm text-emerald-700' : 'text-sm text-destructive'}>
          {message}
        </p>
      ) : null}
    </div>
  );
}
