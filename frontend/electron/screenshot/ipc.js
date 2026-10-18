// IPC surface for screenshot analysis. Everything privileged (capture, key
// decrypt, network) happens here in the main process; the renderer gets only
// results. Every handler rejects callers other than the main app window.
const { DEFAULT_MODEL_KEY, listModels, getModel, resolveModel } = require('./model-config');
const { analyzeScreenshots, AnalysisError } = require('./analysis');
const { validateImages } = require('./image-validation');

const fail = (err) => ({
  ok: false,
  error: {
    code: err?.code || 'UNKNOWN',
    message: err instanceof AnalysisError || err?.code ? err.message : 'Something went wrong. Try again.',
  },
});

/**
 * @param {{ ipcMain: any, getMainWindow: () => any, capture: ReturnType<import('./capture').createCapture>, keyStore: ReturnType<import('./key-store').createKeyStore>, fetchImpl?: typeof fetch }} deps
 */
function registerScreenshotIpc({ ipcMain, getMainWindow, capture, keyStore, envKeys, envHint, fetchImpl }) {
  // A key the user saved in the app wins; otherwise fall back to .env / environment.
  const resolveKey = (provider) => keyStore.get(provider) || envKeys?.get(provider) || null;
  const trusted = (event) => {
    const win = getMainWindow();
    return !!win && !win.isDestroyed() && event.sender === win.webContents;
  };
  const handle = (channel, fn) =>
    ipcMain.handle(channel, async (event, ...args) => {
      if (!trusted(event)) return fail({ code: 'FORBIDDEN', message: 'Not allowed.' });
      try {
        return await fn(...args);
      } catch (err) {
        return fail(err);
      }
    });

  handle('screenshot:get-config', async () => {
    const providers = {};
    const sources = {};
    for (const m of listModels()) {
      const stored = keyStore.has(m.provider);
      const env = !!envKeys?.has(m.provider);
      providers[m.provider] = stored || env;
      sources[m.provider] = stored ? 'stored' : env ? 'env' : null;
    }
    return {
      ok: true,
      models: listModels().map(({ key, provider, displayName, limits }) => ({
        key,
        provider,
        displayName,
        limits,
      })),
      defaultModel: DEFAULT_MODEL_KEY,
      keyStored: providers,
      keySources: sources,
      envHint: envHint || '',
      encryptionAvailable: keyStore.encryptionAvailable(),
    };
  });

  handle('screenshot:list-sources', async () => {
    const win = getMainWindow();
    const own = [];
    try {
      own.push(win.getMediaSourceId());
    } catch {
      // Not available on every build — fall through with no exclusions.
    }
    return { ok: true, sources: await capture.listSources(own) };
  });

  handle('screenshot:capture', async (sourceId) => {
    const image = await capture.captureSource(sourceId);
    capture.flashDisplay(image.displayId);
    delete image.displayId;
    return { ok: true, image };
  });

  handle('screenshot:set-key', async (modelKey, apiKey) => {
    const model = getModel(modelKey);
    if (!model) throw Object.assign(new Error('Unknown model.'), { code: 'UNKNOWN_MODEL' });
    keyStore.set(model.provider, apiKey);
    return { ok: true };
  });

  handle('screenshot:clear-key', async (modelKey) => {
    const model = getModel(modelKey);
    if (!model) throw Object.assign(new Error('Unknown model.'), { code: 'UNKNOWN_MODEL' });
    keyStore.clear(model.provider);
    return { ok: true };
  });

  handle('screenshot:analyze', async (modelKey, images) => {
    const model = resolveModel(modelKey);
    const check = validateImages(images, model.limits);
    if (!check.ok) {
      throw new AnalysisError('VALIDATION', check.errors.map((e) => e.message).join(' '));
    }
    const result = await analyzeScreenshots({
      model,
      images,
      apiKey: resolveKey(model.provider),
      fetchImpl,
    });
    return { ok: true, result, model: model.displayName };
  });
}

module.exports = { registerScreenshotIpc };
