'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { FileUploader } from '@/components/file-uploader';

interface ImportResult { report_type: string; file_name: string; total_rows: number; inserted_rows: number; overwritten_rows: number }

export function AdvertisingReportImport() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();
  async function upload(files: File[]) {
    const file = files[0]; if (!file) return;
    setBusy(true); setMessage(undefined);
    try {
      const form = new FormData(); form.set('file', file);
      const response = await fetch('/api/ad-report-imports', { method: 'POST', body: form });
      const body = (await response.json().catch(() => ({}))) as ImportResult & { detail?: string };
      if (!response.ok) { setMessage(`${file.name}：${body.detail ?? `导入失败（${response.status}）。`}`); return; }
      setMessage(`${file.name} 已共享导入：新增 ${body.inserted_rows} 行，覆盖 ${body.overwritten_rows} 行。`);
    } catch {
      setMessage(`${file.name}：网络异常，暂时无法导入。`);
    } finally {
      setBusy(false);
    }
  }
  return <Card className='space-y-2 p-2'>
    <p className='font-medium'>导入共享广告报表</p>
    <FileUploader accept={{ 'text/csv': ['.csv'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] }} maxSize={20 * 1024 * 1024} maxFiles={1} onUpload={upload} disabled={busy} compact className='h-16' />
    {message && <p className={message.includes('已共享导入') ? 'text-sm text-emerald-700' : 'text-sm text-destructive'}>{message}</p>}
  </Card>;
}
