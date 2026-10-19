// Model catalog for screenshot analysis. The model itself runs on the backend
// (SCREENSHOT_MODEL / GEMINI_API_KEY in the server .env); this entry only
// carries the display name and the client-side upload limits.
const MB = 1024 * 1024;

const MODELS = [
  {
    key: 'gemini-3.5-flash-lite',
    provider: 'google',
    adapter: 'gemini',
    displayName: 'Gemini 3.5 Flash Lite',
    modelId: 'gemini-3.5-flash-lite',
    // Must match backend app/services/screenshot_service.py: requests go
    // through the Vercel /api rewrite (~4.5 MB body cap) and base64 adds ~33%,
    // so ~3 MB of image bytes per analysis keeps the body under that.
    limits: {
      maxImages: 10,
      maxImageBytes: 3 * MB,
      maxTotalBytes: 3 * MB,
      minDimension: 32,
      maxDimension: 8192,
    },
  },
];

const DEFAULT_MODEL_KEY = 'gemini-3.5-flash-lite';

function listModels() {
  return MODELS.map((m) => ({ ...m, limits: { ...m.limits } }));
}

function getModel(key) {
  const found = MODELS.find((m) => m.key === key);
  return found ? { ...found, limits: { ...found.limits } } : null;
}

/** Resolve a (possibly stale/unknown) saved key to a usable model. */
function resolveModel(key) {
  return getModel(key) || getModel(DEFAULT_MODEL_KEY);
}

module.exports = { MODELS, DEFAULT_MODEL_KEY, listModels, getModel, resolveModel };
