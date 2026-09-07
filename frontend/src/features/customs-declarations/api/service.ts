import type { CustomsPreview } from './types';

export class CustomsGenerationError extends Error {
  constructor(public readonly preview: CustomsPreview) {
    super('报关数据校验未通过。');
  }
}

function createFormData(shipmentFile: File, fbaFile: File): FormData {
  const form = new FormData();
  form.set('shipment_file', shipmentFile);
  form.set('fba_file', fbaFile);
  return form;
}

async function readPreview(response: Response): Promise<CustomsPreview> {
  const payload = (await response.json().catch(() => null)) as CustomsPreview | null;
  if (!payload || !Array.isArray(payload.categories)) {
    throw new Error(`分析失败（${response.status}）。`);
  }
  return payload;
}

export async function previewCustomsDeclarations(
  shipmentFile: File,
  fbaFile: File
): Promise<CustomsPreview> {
  const response = await fetch('/api/customs-declarations/preview', {
    method: 'POST',
    body: createFormData(shipmentFile, fbaFile)
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail ?? `分析失败（${response.status}）。`);
  }
  return readPreview(response);
}

function downloadName(response: Response): string {
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (!encoded) return '报关单.zip';
  try {
    return decodeURIComponent(encoded);
  } catch {
    return '报关单.zip';
  }
}

export async function generateCustomsDeclarations(
  shipmentFile: File,
  fbaFile: File
): Promise<void> {
  const response = await fetch('/api/customs-declarations/generate', {
    method: 'POST',
    body: createFormData(shipmentFile, fbaFile)
  });
  if (response.status === 422) {
    throw new CustomsGenerationError(await readPreview(response));
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail ?? `生成失败（${response.status}）。`);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = downloadName(response);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
