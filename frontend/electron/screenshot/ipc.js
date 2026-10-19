// IPC surface for screenshot analysis. Everything privileged (capture,
// network) happens here in the main process; the renderer gets only results.
// Every handler rejects callers other than the main app window. No AI key
// exists on this machine: analysis runs on the backend with the server's key.
const { DEFAULT_MODEL_KEY, listModels, resolveModel } = require('./model-config');
const { analyzeScreenshots, fetchServerReady, AnalysisError } = require('./analysis');
const { validateImages } = require('./image-validation');

const fail = (err) => ({
  ok: false,
  error: {
    code: err?.code || 'UNKNOWN',
    message: err instanceof AnalysisError || err?.code ? err.message : 'Something went wrong. Try again.',
  },
});

const CAPTURE_HOTKEY = 'CommandOrControl+Shift+S';

/**
 * @param {{ ipcMain: any, getMainWindow: () => any, globalShortcut?: any, capture: ReturnType<import('./capture').createCapture>, apiBase: string, fetchImpl?: typeof fetch, focusWindow?: (win: any) => void }} deps
 *   fetchImpl must carry the signed-in session (cookies + CSRF header).
 *   focusWindow forces the app to the foreground WITH keyboard focus.
 */
function registerScreenshotIpc({ ipcMain, getMainWindow, globalShortcut, capture, apiBase, fetchImpl, focusWindow }) {
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
    return {
      ok: true,
      models: listModels().map(({ key, provider, displayName, limits }) => ({
        key,
        provider,
        displayName,
        limits,
      })),
      defaultModel: DEFAULT_MODEL_KEY,
      // true/false = server has / lacks GEMINI_API_KEY; null = not reachable now.
      serverReady: await fetchServerReady({ apiBase, fetchImpl }),
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

  // Ctrl+Shift+S works app-wide while the app runs. On the Screenshots page it
  // opens the source picker with the screen under the cursor preselected
  // (arrow keys change it, Enter captures). Anywhere else it brings the app
  // forward and opens the Screenshots page; the next press then captures.
  let hotkeyOn = false;
  let pageActive = false;
  const bringForward = (win) => {
    // So the picker / page receives arrow and Enter keys. Window focus alone is
    // not enough: the PAGE (webContents) needs keyboard focus too, and Windows
    // won't foreground a background app without the main process's help.
    if (focusWindow) {
      focusWindow(win);
      return;
    }
    if (win.isMinimized?.()) win.restore?.();
    win.show?.();
    win.focus?.();
    win.webContents?.focus?.();
  };
  const onHotkey = async () => {
    const win = getMainWindow();
    if (!win || win.isDestroyed()) return;
    let sourceId = null;
    let error = null;
    try {
      sourceId = await capture.screenSourceAtCursor();
    } catch (err) {
      error = fail(err).error;
    }
    bringForward(win);
    if (!pageActive) {
      // One press from any page: open the Screenshots page AND its picker.
      win.webContents.send('screenshot:open-page', sourceId);
      return;
    }
    win.webContents.send('screenshot:hotkey', sourceId, error);
  };
  const registerHotkey = () => {
    if (!globalShortcut || hotkeyOn) return hotkeyOn;
    try {
      hotkeyOn = globalShortcut.register(CAPTURE_HOTKEY, onHotkey);
    } catch {
      hotkeyOn = false;
    }
    if (!hotkeyOn) console.warn(`[hotkey] ${CAPTURE_HOTKEY} is taken by another app.`);
    return hotkeyOn;
  };
  registerHotkey();

  // A full reload/navigation drops the page's listener — mark the page gone.
  // (Angular hash-route changes are in-place and don't trigger this.)
  const watched = new WeakSet();
  const watchReloads = () => {
    const wc = getMainWindow()?.webContents;
    if (!wc || watched.has(wc)) return;
    watched.add(wc);
    wc.on('did-start-navigation', (_e, _url, isInPlace, isMainFrame) => {
      if (isMainFrame && !isInPlace) pageActive = false;
    });
    wc.once('destroyed', () => (pageActive = false));
  };

  // The Screenshots page reports when it opens (true) and closes (false).
  handle('screenshot:set-hotkey', async (enabled) => {
    watchReloads();
    pageActive = !!enabled;
    return { ok: true, active: registerHotkey() && pageActive };
  });

  handle('screenshot:analyze', async (modelKey, images) => {
    const model = resolveModel(modelKey);
    const check = validateImages(images, model.limits);
    if (!check.ok) {
      throw new AnalysisError('VALIDATION', check.errors.map((e) => e.message).join(' '));
    }
    const result = await analyzeScreenshots({ model, images, apiBase, fetchImpl });
    return { ok: true, result, model: model.displayName };
  });
}

module.exports = { registerScreenshotIpc, CAPTURE_HOTKEY };
