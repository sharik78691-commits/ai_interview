// AI request + response normalisation for screenshot analysis.
// `ADAPTERS` makes providers pluggable; the API key only ever lives in this
// process and is redacted from every error message.
const { validateImages } = require('./image-validation');

const REQUEST_TIMEOUT_MS = 90_000;

const SYSTEM_PROMPT = `You are an interview-preparation coach. The user shares screenshots of interview questions (mock interviews, practice material, or content they own) and wants clear answer guidance to learn from.
Read ALL screenshots in order. Respond ONLY with JSON matching this shape:
{
  "questions": [string],            // every distinct question found, verbatim where possible
  "topic": string,                  // e.g. "System design - caching"
  "seniority": string,              // expected level: "Junior" | "Mid" | "Senior" | "Staff+" with a short reason
  "directAnswer": string,           // concise, direct answer (2-4 sentences)
  "interviewAnswer": string,        // polished, spoken interview-style answer
  "keyPoints": [string],            // key points to mention
  "followUps": [{"question": string, "suggestedResponse": string}],
  "code": null | {                  // only when code is visible
    "explanation": string,
    "complexity": string,           // time and space
    "edgeCases": [string],
    "improvedAnswer": string        // improved solution/answer, code in a fenced block if useful
  }
}
If no question is visible, return an empty "questions" array and explain in "directAnswer". Do not invent content that is not in the screenshots.`;

class AnalysisError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'AnalysisError';
    this.code = code;
  }
}

function redact(text, apiKey) {
  let out = String(text ?? '');
  if (apiKey) out = out.split(apiKey).join('[redacted]');
  return out.replace(/AIza[0-9A-Za-z_-]{20,}/g, '[redacted]');
}

const asString = (v) => (typeof v === 'string' ? v.trim() : '');
const asStringArray = (v) => (Array.isArray(v) ? v.map(asString).filter(Boolean) : []);

/** Coerce arbitrary model JSON into the strict shape the UI renders. */
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

function parseModelJson(text) {
  const cleaned = String(text ?? '')
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '');
  try {
    return JSON.parse(cleaned);
  } catch {
    throw new AnalysisError('BAD_RESPONSE', 'The model returned an unreadable answer. Try again.');
  }
}

const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/models';

const ADAPTERS = {
  gemini: {
    buildRequest(model, images) {
      const parts = [];
      images.forEach((img, i) => {
        parts.push({ text: `Screenshot ${i + 1} of ${images.length}:` });
        parts.push({ inline_data: { mime_type: img.mimeType, data: img.data } });
      });
      parts.push({ text: 'Analyze these screenshots and respond with the JSON described.' });
      return {
        url: `${GEMINI_BASE}/${encodeURIComponent(model.modelId)}:generateContent`,
        headers: { 'Content-Type': 'application/json' },
        // Key goes in a header, never the URL, so it cannot leak via logs.
        authHeader: (apiKey) => ({ 'x-goog-api-key': apiKey }),
        body: {
          systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
          contents: [{ role: 'user', parts }],
          generationConfig: { responseMimeType: 'application/json', temperature: 0.4 },
        },
      };
    },
    extractText(json) {
      const block = json?.promptFeedback?.blockReason;
      if (block) {
        throw new AnalysisError(
          'BLOCKED',
          `The request was blocked by the model's safety filter (${block}).`,
        );
      }
      const cand = json?.candidates?.[0];
      const text = (cand?.content?.parts ?? []).map((p) => p?.text ?? '').join('');
      if (!text) {
        if (cand?.finishReason && cand.finishReason !== 'STOP') {
          throw new AnalysisError(
            'BLOCKED',
            `The model stopped without an answer (${cand.finishReason}).`,
          );
        }
        throw new AnalysisError('BAD_RESPONSE', 'The model returned an empty answer. Try again.');
      }
      return text;
    },
  },
};

function mapHttpError(status, bodyText) {
  if (status === 401 || status === 403 || /API_KEY_INVALID|API key not valid/i.test(bodyText)) {
    return new AnalysisError('INVALID_KEY', 'The API key was rejected. Check it in AI Model settings.');
  }
  if (status === 429) {
    return new AnalysisError('RATE_LIMITED', 'Rate limit or quota reached. Wait a moment and try again.');
  }
  if (status === 413) {
    return new AnalysisError('TOO_LARGE', 'The request was too large. Remove some screenshots.');
  }
  if (status >= 500) {
    return new AnalysisError('PROVIDER_ERROR', 'The AI provider is having trouble. Try again shortly.');
  }
  return new AnalysisError('REQUEST_FAILED', `The AI request failed (HTTP ${status}).`);
}

/**
 * @param {{ model: object, images: object[], apiKey: string|null, fetchImpl?: typeof fetch, timeoutMs?: number }} args
 */
async function analyzeScreenshots({
  model,
  images,
  apiKey,
  fetchImpl = fetch,
  timeoutMs = REQUEST_TIMEOUT_MS,
}) {
  if (!apiKey) {
    throw new AnalysisError('NO_KEY', 'Add your API key in AI Model settings first.');
  }
  const check = validateImages(images, model.limits);
  if (!check.ok) throw new AnalysisError('VALIDATION', check.errors.map((e) => e.message).join(' '));

  const adapter = ADAPTERS[model.adapter];
  if (!adapter) {
    throw new AnalysisError('UNSUPPORTED_MODEL', `Unsupported model adapter "${model.adapter}".`);
  }

  const req = adapter.buildRequest(model, images);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetchImpl(req.url, {
      method: 'POST',
      headers: { ...req.headers, ...req.authHeader(apiKey) },
      body: JSON.stringify(req.body),
      signal: controller.signal,
    });
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new AnalysisError('TIMEOUT', 'The AI request timed out. Try again with fewer screenshots.');
    }
    throw new AnalysisError('NETWORK', 'Could not reach the AI provider. Check your connection.');
  } finally {
    clearTimeout(timer);
  }

  const bodyText = redact(await res.text().catch(() => ''), apiKey);
  if (!res.ok) throw mapHttpError(res.status, bodyText);

  let json;
  try {
    json = JSON.parse(bodyText);
  } catch {
    throw new AnalysisError('BAD_RESPONSE', 'The AI provider returned an unreadable response.');
  }
  return normalizeResult(parseModelJson(adapter.extractText(json)));
}

module.exports = {
  AnalysisError,
  SYSTEM_PROMPT,
  ADAPTERS,
  normalizeResult,
  analyzeScreenshots,
  redact,
};
