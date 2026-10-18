// Validates screenshots before they are sent to a model. Enforced in the main
// process (authoritative); the Angular app runs the same rules for fast UX.
const ALLOWED_MIME = ['image/png', 'image/jpeg', 'image/webp'];

/** Decoded byte size of a base64 string (no data-URL prefix). */
function base64Bytes(data) {
  if (typeof data !== 'string' || !data.length) return 0;
  const pad = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0;
  return Math.floor((data.length * 3) / 4) - pad;
}

const fmtMb = (n) => `${(n / (1024 * 1024)).toFixed(1)} MB`;

/**
 * @param {{mimeType:string,data:string,width:number,height:number}[]} images
 * @param {{maxImages:number,maxImageBytes:number,maxTotalBytes:number,minDimension:number,maxDimension:number}} limits
 * @returns {{ok:boolean, errors:{code:string,message:string,index?:number}[], totalBytes:number}}
 */
function validateImages(images, limits) {
  const errors = [];
  if (!Array.isArray(images) || images.length === 0) {
    return {
      ok: false,
      totalBytes: 0,
      errors: [{ code: 'EMPTY', message: 'Capture at least one screenshot first.' }],
    };
  }
  if (images.length > limits.maxImages) {
    errors.push({
      code: 'TOO_MANY',
      message: `Too many screenshots (${images.length}). The maximum is ${limits.maxImages}.`,
    });
  }
  let total = 0;
  images.forEach((img, index) => {
    const n = index + 1;
    if (!img || !ALLOWED_MIME.includes(img.mimeType)) {
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
        message: `Screenshot ${n} is ${fmtMb(bytes)}; the limit is ${fmtMb(limits.maxImageBytes)} per image.`,
      });
    }
    const { width, height } = img;
    if (!Number.isFinite(width) || !Number.isFinite(height)) {
      errors.push({ code: 'BAD_DIMENSIONS', index, message: `Screenshot ${n}: unknown dimensions.` });
    } else if (Math.min(width, height) < limits.minDimension) {
      errors.push({
        code: 'DIMENSIONS_TOO_SMALL',
        index,
        message: `Screenshot ${n} is too small (${width}×${height}).`,
      });
    } else if (Math.max(width, height) > limits.maxDimension) {
      errors.push({
        code: 'DIMENSIONS_TOO_LARGE',
        index,
        message: `Screenshot ${n} is too large (${width}×${height}); max ${limits.maxDimension}px per side.`,
      });
    }
  });
  if (total > limits.maxTotalBytes) {
    errors.push({
      code: 'TOTAL_TOO_LARGE',
      message: `Total size ${fmtMb(total)} exceeds the ${fmtMb(limits.maxTotalBytes)} request limit. Remove some screenshots.`,
    });
  }
  return { ok: errors.length === 0, errors, totalBytes: total };
}

module.exports = { ALLOWED_MIME, base64Bytes, validateImages };
