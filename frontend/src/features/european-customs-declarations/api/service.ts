import type { EuropeanCustomsPreview } from './types';

export class EuropeanCustomsGenerationError extends Error {
  constructor(public readonly preview: EuropeanCustomsPreview) {
    super('欧洲报关数据校验未通过。');
  }
}

function createFormData(file: File): FormData {
  const data = new FormData();
  data.set('file', file);
  return data;
}

async function readPreview(response: Response): Promise<EuropeanCustomsPreview> {
  const payload = (await response.json().catch(() => null)) as EuropeanCustomsPreview | null;
  if (!payload || !Array.isArray(payload.categories)) {
    throw new Error(`分析失败（${response.status}）。`);
  }
  return payload;
}

export async function previewEuropeanCustomsDeclarations(file: File): Promise<EuropeanCustomsPreview> {
  const response = await fetch('/api/european-customs-declarations/preview', {
    method: 'POST',
    body: createFormData(file)
  });
  if (!response.ok) throw new Error(`分析失败（${response.status}）。`);
  return readPreview(response);
}

export async function generateEuropeanCustomsDeclarations(file: File): Promise<void> {
  const response = await fetch('/api/european-customs-declarations/generate', {
    method: 'POST',
    body: createFormData(file)
  });
  if (response.status === 422) throw new EuropeanCustomsGenerationError(await readPreview(response));
  if (!response.ok) throw new Error(`生成失败（${response.status}）。`);
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = encoded ? decodeURIComponent(encoded) : '欧洲报关表.zip';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
