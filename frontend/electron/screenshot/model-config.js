// Model catalog for screenshot analysis. Add a new entry (and, if the provider
// is new, an adapter in analysis.js `ADAPTERS`) to support another model — no
// other module hardcodes a model name.
const MB = 1024 * 1024;

const MODELS = [
  {
    key: 'gemini-3.5-flash-lite',
    provider: 'google',
    adapter: 'gemini',
    displayName: 'Gemini 3.5 Flash Lite',
    modelId: 'gemini-3.5-flash-lite',
    // Conservative client-side caps. Gemini accepts far more images per request
    // but inline requests are capped at ~20 MB total (base64 adds ~33%), so the
    // raw-bytes budget below keeps the encoded request safely under that.
    limits: {
      maxImages: 16,
      maxImageBytes: 7 * MB,
      maxTotalBytes: 14 * MB,
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
