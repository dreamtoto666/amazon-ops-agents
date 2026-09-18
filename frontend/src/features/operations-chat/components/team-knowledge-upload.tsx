'use client';

import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { FileUploader } from '@/components/file-uploader';

interface KnowledgeStatus {
  status: string;
  document_count: number;
  chunk_count: number;
  error: string | null;
}

const NEUTRAL_STATUS_TEXT: Record<string, string> = {
  empty: '尚未上传团队知识库。',
  indexing: '正在索引新的知识库版本，完成后会自动替换当前版本。',
  unavailable: '知识库暂时不可用，请稍后重试。'
};

export function TeamKnowledgeUpload() {
  const [status, setStatus] = useState<KnowledgeStatus>();
  const [statusError, setStatusError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();
  const [isAdmin, setIsAdmin] = useState<boolean>();

  const loadStatus = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch('/api/team-knowledge/vault');
      const body = (await response.json().catch(() => undefined)) as KnowledgeStatus | undefined;
      // 读取失败必须显示错误，不能显示成「尚未上传」。
      if (!response.ok || !body) {
        setStatus(undefined);
        setStatusError(`无法读取知识库状态（${response.status}）。`);
        return;
      }
      setStatusError(undefined);
      setStatus(body);
    } catch {
      setStatus(undefined);
      setStatusError('无法读取知识库状态：网络异常。');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch('/api/auth/me');
        const user = (await response.json().catch(() => undefined)) as
          | { role?: string }
          | undefined;
        if (cancelled) return;
        if (!response.ok || !user) {
          setIsAdmin(false);
          return;
        }
        const hasAdminRole = user.role === 'admin';
        setIsAdmin(hasAdminRole);
        if (hasAdminRole) {
          await loadStatus();
        }
      } catch {
        if (cancelled) return;
        setIsAdmin(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadStatus]);

  if (isAdmin !== true) {
    return null;
  }

  async function upload(files: File[]): Promise<void> {
    const file = files[0];
    if (!file) return;
    setBusy(true);
    setMessage(undefined);
    try {
      const form = new FormData();
      form.set('file', file);
      const response = await fetch('/api/team-knowledge/vault', { method: 'POST', body: form });
      const body = (await response.json().catch(() => ({}))) as KnowledgeStatus & { detail?: string };
      if (!response.ok) {
        setMessage(`${file.name}：${body.detail ?? `上传失败（${response.status}）。`}`);
        return;
      }
      setStatus(body);
      setStatusError(undefined);
      setMessage(`${file.name} 已更新团队知识库。`);
    } catch {
      setMessage(`${file.name}：网络异常，暂时无法上传。`);
    } finally {
      setBusy(false);
    }
  }

  function renderStatus() {
    if (statusError) {
      return <p className='text-xs text-destructive'>{statusError}</p>;
    }
    if (!status) {
      return <p className='text-xs text-muted-foreground'>正在读取知识库状态…</p>;
    }
    if (status.status === 'active') {
      return (
        <p className='text-xs text-muted-foreground'>
          {status.document_count} 篇笔记 · {status.chunk_count} 个片段
        </p>
      );
    }
    if (status.status === 'failed') {
      return (
        <p className='text-xs text-destructive'>
          上一次上传索引失败：{status.error ?? '原因未知'}。
        </p>
      );
    }
    return (
      <p className='text-xs text-muted-foreground'>
        {NEUTRAL_STATUS_TEXT[status.status] ?? `知识库状态：${status.status}`}
      </p>
    );
  }

  return (
    <Card className='space-y-2 p-2'>
      <p className='font-medium'>团队知识库</p>
      <FileUploader
        accept={{ 'application/zip': ['.zip'] }}
        maxSize={50 * 1024 * 1024}
        maxFiles={1}
        onUpload={upload}
        disabled={busy}
        compact
        className='h-16'
      />
      {renderStatus()}
      {message && (
        <p
          className={
            message.includes('已更新')
              ? 'text-sm text-emerald-700'
              : 'text-sm text-destructive'
          }
        >
          {message}
        </p>
      )}
    </Card>
  );
}
