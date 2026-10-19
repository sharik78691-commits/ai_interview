// Screenshot analysis request + response normalisation.
//
// The desktop app holds NO AI key. Screenshots go to our backend
// (POST /api/screenshots/analyze) with the signed-in user's session; the
// backend calls the model with GEMINI_API_KEY from the SERVER's .env.
const { validateImages } = require('./image-validation');

const REQUEST_TIMEOUT_MS = 120_000;

class AnalysisError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'AnalysisError';
    this.code = code;
  }
}

/** Defence in depth: never echo anything that looks like a Google key. */
function redact(text, apiKey) {
  let out = String(text ?? '');
  if (apiKey) out = out.split(apiKey).join('[redacted]');
  return out.replace(/AIza[0-9A-Za-z_-]{20,}/g, '[redacted]');
}

const asString = (v) => (typeof v === 'string' ? v.trim() : '');
const asStringArray = (v) => (Array.isArray(v) ? v.map(asString).filter(Boolean) : []);

/** Coerce the server's result into the strict shape the UI renders. */
function normalizeResult(raw) {
  if (!raw || typeof raw !== 'object') {
    throw new AnalysisError('BAD_RESPONSE', 'The model returned an unreadable answer. Try again.');
  }
  const code =
    raw.code && typeof raw.code === 'object'
      ? {
          explanation: asString(raw.code.explanation),
          complexity: asString(raw.code.complexity),
          edgeCases: asStringArray(raw.code.edgeCases),
          improvedAnswer: asString(raw.code.improvedAnswer),
        }
      : null;
  return {
    questions: asStringArray(raw.questions),
    topic: asString(raw.topic),
    seniority: asString(raw.seniority),
    directAnswer: asString(raw.directAnswer),
    interviewAnswer: asString(raw.interviewAnswer),
    keyPoints: asStringArray(raw.keyPoints),
    followUps: (Array.isArray(raw.followUps) ? raw.followUps : [])
      .map((f) => ({
        question: asString(f?.question),
        suggestedResponse: asString(f?.suggestedResponse),
      }))
      .filter((f) => f.question),
    code:
      code && (code.explanation || code.complexity || code.edgeCases.length || code.improvedAnswer)
        ? code
        : null,
  };
}

/** Server error body is { detail: { code, message } } (or a plain FastAPI detail). */
function mapServerError(status, body) {
  const detail = body?.detail;
  if (detail && typeof detail === 'object' && detail.code) {
    return new AnalysisError(String(detail.code), redact(detail.message || 'The analysis failed.'));
  }
  if (status === 401) {
    return new AnalysisError('NOT_SIGNED_IN', 'Sign in again to analyze screenshots.');
  }
  if (status === 403) {
    return new AnalysisError('SESSION_EXPIRED', 'Your session expired. Sign in again and retry.');
  }
  if (status === 413) {
    return new AnalysisError('TOO_LARGE', 'The screenshots are too large together. Remove some.');
  }
  if (status === 429) {
    return new AnalysisError('RATE_LIMITED', 'Too many analyses in a short time. Wait a moment and try again.');
  }
  if (status >= 500) {
    return new AnalysisError('SERVER_ERROR', 'The server is having trouble. Try again shortly.');
  }
  return new AnalysisError('REQUEST_FAILED', `The analysis request failed (HTTP ${status}).`);
}

/**
 * @param {{ model: object, images: object[], apiBase: string, fetchImpl?: typeof fetch, timeoutMs?: number }} args
 */
async function analyzeScreenshots({
  model,
  images,
  apiBase,
  fetchImpl = fetch,
  timeoutMs = REQUEST_TIMEOUT_MS,
}) {
  const check = validateImages(images, model.limits);
  if (!check.ok) throw new AnalysisError('VALIDATION', check.errors.map((e) => e.message).join(' '));

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetchImpl(`${apiBase}/api/screenshots/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Only the image bytes + type leave the machine (no source names).
      body: JSON.stringify({ images: images.map((i) => ({ mimeType: i.mimeType, data: i.data })) }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new AnalysisError('TIMEOUT', 'The analysis timed out. Try again with fewer screenshots.');
    }
    throw new AnalysisError('NETWORK', 'Could not reach the server. Check your connection.');
  } finally {
    clearTimeout(timer);
  }

  const text = await res.text().catch(() => '');
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) throw mapServerError(res.status, body);
  if (!body || body.ok !== true) {
    throw new AnalysisError('BAD_RESPONSE', 'The server returned an unreadable response.');
  }
  return normalizeResult(body.result);
}

/** GET /api/screenshots/config — true/false when known, null when unreachable. */
async function fetchServerReady({ apiBase, fetchImpl = fetch, timeoutMs = 10_000 }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${apiBase}/api/screenshots/config`, { signal: controller.signal });
    if (!res.ok) return null;
    const body = JSON.parse(await res.text());
    return typeof body?.configured === 'boolean' ? body.configured : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = {
  AnalysisError,
  normalizeResult,
  analyzeScreenshots,
  fetchServerReady,
  redact,
};
