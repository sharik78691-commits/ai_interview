import { CapturedImage, ScreenshotLimits } from './screenshot.models';

export const ALLOWED_MIME = ['image/png', 'image/jpeg', 'image/webp'];

export interface ValidationIssue {
  code: string;
  message: string;
  index?: number;
}

export interface ValidationResult {
  ok: boolean;
  errors: ValidationIssue[];
  totalBytes: number;
}

/** Decoded size of a base64 string (no data-URL prefix). */
export function base64Bytes(data: string): number {
  if (!data) return 0;
  const pad = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0;
  return Math.floor((data.length * 3) / 4) - pad;
}

const mb = (n: number) => `${(n / (1024 * 1024)).toFixed(1)} MB`;

/**
 * Mirrors electron/screenshot/image-validation.js. The main process re-checks
 * every request (authoritative); this copy gives instant feedback in the UI.
 */
export function validateImages(
  images: Pick<CapturedImage, 'mimeType' | 'data' | 'width' | 'height'>[],
  limits: ScreenshotLimits,
): ValidationResult {
  if (!images.length) {
    return {
      ok: false,
      totalBytes: 0,
      errors: [{ code: 'EMPTY', message: 'Capture at least one screenshot first.' }],
    };
  }
  const errors: ValidationIssue[] = [];
  if (images.length > limits.maxImages) {
    errors.push({
      code: 'TOO_MANY',
      message: `Too many screenshots (${images.length}). The maximum is ${limits.maxImages}.`,
    });
  }
  let total = 0;
  images.forEach((img, index) => {
    const n = index + 1;
    if (!ALLOWED_MIME.includes(img.mimeType)) {
      errors.push({ code: 'BAD_TYPE', index, message: `Screenshot ${n}: unsupported image type.` });
      return;
    }
    const bytes = base64Bytes(img.data);
    total += bytes;
    if (bytes === 0) {
      errors.push({ code: 'EMPTY_IMAGE', index, message: `Screenshot ${n} is empty.` });
    } else if (bytes > limits.maxImageBytes) {
      errors.push({
        code: 'IMAGE_TOO_LARGE',
        index,
        message: `Screenshot ${n} is ${mb(bytes)}; the limit is ${mb(limits.maxImageBytes)} per image.`,
      });
    }
    if (!Number.isFinite(img.width) || !Number.isFinite(img.height)) {
      errors.push({ code: 'BAD_DIMENSIONS', index, message: `Screenshot ${n}: unknown dimensions.` });
    } else if (Math.min(img.width, img.height) < limits.minDimension) {
      errors.push({
        code: 'DIMENSIONS_TOO_SMALL',
        index,
        message: `Screenshot ${n} is too small (${img.width}×${img.height}).`,
      });
    } else if (Math.max(img.width, img.height) > limits.maxDimension) {
      errors.push({
        code: 'DIMENSIONS_TOO_LARGE',
        index,
        message: `Screenshot ${n} is too large (${img.width}×${img.height}); max ${limits.maxDimension}px per side.`,
      });
    }
  });
  if (total > limits.maxTotalBytes) {
    errors.push({
      code: 'TOTAL_TOO_LARGE',
      message: `Total size ${mb(total)} exceeds the ${mb(limits.maxTotalBytes)} request limit. Remove some screenshots.`,
    });
  }
  return { ok: errors.length === 0, errors, totalBytes: total };
}
