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
  EuropeanCustomsGenerationError,
  generateEuropeanCustomsDeclarations,
  previewEuropeanCustomsDeclarations
} from '../api/service';
import type { EuropeanCustomsCategoryPreview, EuropeanCustomsIssue, EuropeanCustomsPreview } from '../api/types';

const MAX_FILE_SIZE = 20 * 1024 * 1024;

function decimal(value: string): string {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function CategoryCard({ category }: { category: EuropeanCustomsCategoryPreview }) {
  return (
    <Card className={category.generates_file ? '' : 'opacity-65'}>
      <CardHeader className='flex flex-row items-start justify-between space-y-0 pb-3'>
        <div>
          <CardTitle className='text-base'>{category.channel} × {category.prefix} × {category.country}</CardTitle>
          <p className='mt-1 text-xs text-muted-foreground'>贸易方式 {category.trade_mode}</p>
        </div>
        <Badge variant={category.generates_file ? 'default' : 'secondary'}>{category.generates_file ? '将生成' : '空分类'}</Badge>
      </CardHeader>
      <CardContent className='grid grid-cols-2 gap-3 text-sm'>
        <Metric label='货件' value={`${category.shipment_count} 个`} />
        <Metric label='商品行' value={`${category.item_count} 行`} />
        <Metric label='箱数' value={`${category.box_count} 箱`} />
        <Metric label='毛重' value={`${decimal(category.gross_weight)} kg`} />
        <Metric label='净重' value={`${decimal(category.net_weight)} kg`} />
        <Metric label='申报金额' value={`$${decimal(category.amount)}`} />
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><p className='text-xs text-muted-foreground'>{label}</p><p className='font-medium tabular-nums'>{value}</p></div>;
}

function Issues({ title, issues, warning = false }: { title: string; issues: EuropeanCustomsIssue[]; warning?: boolean }) {
  if (!issues.length) return null;
  return (
    <Alert variant={warning ? 'default' : 'destructive'}>
      {warning ? <Icons.warning className='size-4' /> : <Icons.alertCircle className='size-4' />}
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription><ul className='mt-2 space-y-1'>{issues.map((issue, index) => <li key={`${issue.code}-${issue.row}-${index}`}>{issue.sheet ? `${issue.sheet}${issue.row ? ` 第 ${issue.row} 行` : ''}：` : ''}{issue.message}</li>)}</ul></AlertDescription>
    </Alert>
  );
}

export function EuropeanCustomsDeclarationWorkbench() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<EuropeanCustomsPreview | null>(null);
  const [busy, setBusy] = useState<'preview' | 'generate' | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function selectFile(event: ChangeEvent<HTMLInputElement>): void {
    setFile(event.target.files?.[0] ?? null);
    setPreview(null);
    setMessage(null);
  }

  async function handlePreview(): Promise<void> {
    if (!file) return setMessage('请先选择包含 Sheet1 的 Excel 文件。');
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > MAX_FILE_SIZE) return setMessage('仅支持不超过 20MB 的 .xlsx 文件。');
    setBusy('preview'); setMessage(null);
    try { setPreview(await previewEuropeanCustomsDeclarations(file)); }
    catch (error) { setMessage(error instanceof Error ? error.message : '分析失败，请稍后重试。'); }
    finally { setBusy(null); }
  }

  async function handleGenerate(): Promise<void> {
    if (!file || !preview?.can_generate) return;
    setBusy('generate'); setMessage(null);
    try { await generateEuropeanCustomsDeclarations(file); setMessage('欧洲报关表 ZIP 已生成并开始下载。'); }
    catch (error) {
      if (error instanceof EuropeanCustomsGenerationError) setPreview(error.preview);
      setMessage(error instanceof Error ? error.message : '生成失败，请稍后重试。');
    } finally { setBusy(null); }
  }

  return (
    <div className='space-y-6'>
      <div className='grid gap-3 rounded-xl border bg-muted/20 p-4 text-sm md:grid-cols-3'>
        <div className='flex items-center gap-2'><Icons.calendar className='size-4 text-muted-foreground' />出口日期自动使用上海时区今天</div>
        <div className='flex items-center gap-2'><Icons.edit className='size-4 text-muted-foreground' />沿用原报关单模板，运费下载后填写</div>
        <div className='flex items-center gap-2'><Icons.fileZip className='size-4 text-muted-foreground' />24 类中仅生成有数据的表格</div>
      </div>
      <Card><CardHeader><CardTitle>上传欧洲货件表</CardTitle></CardHeader><CardContent className='space-y-5'>
        <div className='rounded-xl border bg-muted/20 p-4'><Label htmlFor='european-file' className='text-base'>Excel 文件</Label><p className='mt-1 text-sm text-muted-foreground'>仅读取 Sheet1，按华贸卡航、华贸海运、华贸空运、一八海运及红福/其它、国家拆分。</p><Input id='european-file' className='mt-3' type='file' accept='.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' disabled={busy !== null} onChange={selectFile} /><p className='mt-2 text-xs text-muted-foreground'>{file ? `已选择：${file.name}` : '仅支持 .xlsx，单文件不超过 20MB'}</p></div>
        <Button type='button' onClick={() => void handlePreview()} disabled={busy !== null}>{busy === 'preview' ? <Icons.spinner className='mr-2 size-4 animate-spin' /> : <Icons.search className='mr-2 size-4' />}分析文件</Button>
      </CardContent></Card>
      {preview ? <><div className='grid gap-4 xl:grid-cols-3'>{preview.categories.map((category) => <CategoryCard key={`${category.channel}-${category.prefix}-${category.country}`} category={category} />)}</div><Issues title={`有 ${preview.errors.length} 项问题需要处理`} issues={preview.errors} /><Issues title={`有 ${preview.warnings.length} 项提示`} issues={preview.warnings} warning /><Button type='button' size='lg' disabled={!preview.can_generate || busy !== null} onClick={() => void handleGenerate()}>{busy === 'generate' ? <Icons.spinner className='mr-2 size-4 animate-spin' /> : <Icons.fileZip className='mr-2 size-4' />}生成并下载 ZIP</Button></> : null}
      {message ? <p className={message.includes('已生成') ? 'text-sm text-emerald-700' : 'text-sm text-destructive'}>{message}</p> : null}
    </div>
  );
}
