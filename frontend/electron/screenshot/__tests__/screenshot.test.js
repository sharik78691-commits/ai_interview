// Run with: npm run test:electron   (node --test, no extra dependencies)
const test = require('node:test');
const assert = require('node:assert/strict');

const { DEFAULT_MODEL_KEY, listModels, resolveModel } = require('../model-config');
const { validateImages, base64Bytes } = require('../image-validation');
const { analyzeScreenshots, fetchServerReady, redact } = require('../analysis');
const { createCapture } = require('../capture');
const { registerScreenshotIpc } = require('../ipc');

const LIMITS = {
  maxImages: 3,
  maxImageBytes: 1000,
  maxTotalBytes: 1500,
  minDimension: 32,
  maxDimension: 4096,
};
const b64 = (bytes) => Buffer.alloc(bytes, 1).toString('base64');
const img = (bytes = 300, extra = {}) => ({
  mimeType: 'image/png',
  data: b64(bytes),
  width: 800,
  height: 600,
  ...extra,
});

// ---- model default selection ----
test('default model is Gemini 3.5 Flash Lite', () => {
  assert.equal(DEFAULT_MODEL_KEY, 'gemini-3.5-flash-lite');
  assert.equal(resolveModel(undefined).displayName, 'Gemini 3.5 Flash Lite');
  assert.equal(resolveModel('does-not-exist').key, DEFAULT_MODEL_KEY);
  assert.ok(listModels().length >= 1);
});

// ---- multi-image validation ----
test('validation: accepts a good batch', () => {
  assert.equal(validateImages([img(), img()], LIMITS).ok, true);
});
test('validation: rejects empty, too many, oversize, total, type and dimensions', () => {
  const codes = (imgs) => validateImages(imgs, LIMITS).errors.map((e) => e.code);
  assert.deepEqual(codes([]), ['EMPTY']);
  assert.ok(codes([img(100), img(100), img(100), img(100)]).includes('TOO_MANY'));
  assert.ok(codes([img(2000)]).includes('IMAGE_TOO_LARGE'));
  assert.ok(codes([img(900), img(900)]).includes('TOTAL_TOO_LARGE'));
  assert.ok(codes([img(100, { mimeType: 'application/pdf' })]).includes('BAD_TYPE'));
  assert.ok(codes([img(100, { width: 10, height: 10 })]).includes('DIMENSIONS_TOO_SMALL'));
  assert.ok(codes([img(100, { width: 9000 })]).includes('DIMENSIONS_TOO_LARGE'));
  assert.ok(codes([img(100, { width: NaN })]).includes('BAD_DIMENSIONS'));
});
test('base64Bytes matches Buffer length', () => {
  for (const n of [1, 2, 3, 100, 1001]) assert.equal(base64Bytes(b64(n)), n);
});

// ---- backend AI request + error states (no key on this machine) ----
const API = 'https://api.test';
const model = () => resolveModel(DEFAULT_MODEL_KEY);
const goodResult = {
  questions: ['What is a closure?'],
  topic: 'JavaScript',
  seniority: 'Mid',
  directAnswer: 'A function plus its lexical scope.',
  interviewAnswer: 'A closure is...',
  keyPoints: ['lexical scope'],
  followUps: [{ question: 'Memory leaks?', suggestedResponse: 'Yes if...' }],
  code: null,
};
const goodBody = { ok: true, result: goodResult, model: 'gemini-3.5-flash-lite' };
const fakeFetch = (status, body) => async () => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
});
const run = (fetchImpl) => analyzeScreenshots({ model: model(), images: [img(500)], apiBase: API, fetchImpl });

test('analysis: posts images to OUR backend with no API key, returns normalized result', async () => {
  let seen;
  const result = await run(async (url, opts) => {
    seen = { url, opts };
    return fakeFetch(200, goodBody)();
  });
  assert.equal(seen.url, `${API}/api/screenshots/analyze`);
  assert.equal(seen.opts.method, 'POST');
  assert.ok(!/goog|key/i.test(JSON.stringify(seen.opts.headers)), 'no key header leaves the app');
  const body = JSON.parse(seen.opts.body);
  assert.deepEqual(Object.keys(body.images[0]).sort(), ['data', 'mimeType']);
  assert.equal(result.topic, 'JavaScript');
  assert.equal(result.code, null);
  assert.equal(result.followUps.length, 1);
});
test('analysis: server error states map to clear codes', async () => {
  const code = async (p) => p.then(() => 'ok', (e) => e.code);
  const detail = (c) => ({ detail: { code: c, message: 'x' } });
  assert.equal(await code(run(fakeFetch(401, { detail: 'Not authenticated' }))), 'NOT_SIGNED_IN');
  assert.equal(await code(run(fakeFetch(403, { detail: 'CSRF' }))), 'SESSION_EXPIRED');
  assert.equal(await code(run(fakeFetch(503, detail('NOT_CONFIGURED')))), 'NOT_CONFIGURED');
  assert.equal(await code(run(fakeFetch(429, { detail: 'Too many attempts.' }))), 'RATE_LIMITED');
  assert.equal(await code(run(fakeFetch(413, ''))), 'TOO_LARGE');
  assert.equal(await code(run(fakeFetch(502, detail('PROVIDER_ERROR')))), 'PROVIDER_ERROR');
  assert.equal(await code(run(fakeFetch(500, '<html>'))), 'SERVER_ERROR');
  assert.equal(await code(run(fakeFetch(200, 'not json'))), 'BAD_RESPONSE');
  assert.equal(await code(run(async () => { throw new TypeError('fetch failed'); })), 'NETWORK');
  assert.equal(
    await code(analyzeScreenshots({ model: model(), images: [], apiBase: API, fetchImpl: fakeFetch(200, goodBody) })),
    'VALIDATION',
  );
  const slow = (_u, { signal }) =>
    new Promise((_r, rej) => signal.addEventListener('abort', () => rej(Object.assign(new Error('a'), { name: 'AbortError' }))));
  assert.equal(
    await code(analyzeScreenshots({ model: model(), images: [img(500)], apiBase: API, fetchImpl: slow, timeoutMs: 20 })),
    'TIMEOUT',
  );
});
test('analysis: server messages are passed through with keys redacted', async () => {
  const err = await run(fakeFetch(502, { detail: { code: 'PROVIDER_ERROR', message: 'bad AIza' + 'x'.repeat(30) } })).catch((e) => e);
  assert.ok(!err.message.includes('xxxx'));
  assert.equal(redact('bad key AIzaSECRET_KEY_123456 here', 'AIzaSECRET_KEY_123456'), 'bad key [redacted] here');
});
test('server readiness: true/false from the backend, null when unreachable', async () => {
  assert.equal(await fetchServerReady({ apiBase: API, fetchImpl: fakeFetch(200, { configured: true }) }), true);
  assert.equal(await fetchServerReady({ apiBase: API, fetchImpl: fakeFetch(200, { configured: false }) }), false);
  assert.equal(await fetchServerReady({ apiBase: API, fetchImpl: fakeFetch(401, {}) }), null);
  assert.equal(await fetchServerReady({ apiBase: API, fetchImpl: async () => { throw new Error('offline'); } }), null);
});
test('client limits fit the backend + Vercel body budget', () => {
  const { limits } = model();
  assert.ok(limits.maxImages <= 10);
  assert.ok((limits.maxTotalBytes * 4) / 3 < 4.5 * 1024 * 1024);
});

// ---- capture flow + IPC gating ----
function fakeElectron({ sources, trustedSender = true }) {
  const handlers = {};
  const flashed = [];
  const nativeImg = (w, h, empty = false) => ({
    isEmpty: () => empty,
    getSize: () => ({ width: w, height: h }),
    toDataURL: () => 'data:image/png;base64,AAAA',
    resize: ({ width, height }) => nativeImg(width, height),
    toJPEG: () => Buffer.from('jpegbytes'),
  });
  const desktopCapturer = {
    calls: 0,
    getSources: async ({ thumbnailSize }) =>
      sources.map((s) => ({ ...s, thumbnail: nativeImg(thumbnailSize.width, thumbnailSize.height, s.empty) })),
  };
  const screen = {
    getAllDisplays: () => [{ id: 1, size: { width: 3840, height: 2160 }, scaleFactor: 1, bounds: { x: 0, y: 0, width: 3840, height: 2160 } }],
    getPrimaryDisplay: () => ({ bounds: { x: 0, y: 0, width: 100, height: 100 } }),
  };
  function BrowserWindow() { flashed.push(1); throw new Error('no window in tests'); }
  const capture = createCapture({ desktopCapturer, screen, BrowserWindow });
  const sender = {};
  const win = { isDestroyed: () => false, webContents: sender, getMediaSourceId: () => 'window:self' };
  const calls = [];
  registerScreenshotIpc({
    ipcMain: { handle: (ch, fn) => (handlers[ch] = fn), removeHandler() {} },
    getMainWindow: () => win,
    capture,
    apiBase: API,
    fetchImpl: async (url) => {
      calls.push(url);
      return url.endsWith('/config') ? fakeFetch(200, { configured: true })() : fakeFetch(200, goodBody)();
    },
  });
  const invoke = (ch, ...args) => handlers[ch]({ sender: trustedSender ? sender : {} }, ...args);
  return { invoke, calls };
}

const SOURCES = [
  { id: 'screen:1:0', name: 'Entire Screen', display_id: '1' },
  { id: 'window:self', name: 'audio O control', display_id: '' },
  { id: 'window:42:0', name: 'Interview prep', display_id: '' },
];

test('capture flow: list hides own window, capture returns downscaled JPEG, nothing happens uninvited', async () => {
  const { invoke } = fakeElectron({ sources: SOURCES });
  const listed = await invoke('screenshot:list-sources');
  assert.deepEqual(listed.sources.map((s) => s.id), ['screen:1:0', 'window:42:0']);
  const cap = await invoke('screenshot:capture', 'screen:1:0');
  assert.equal(cap.ok, true);
  assert.equal(cap.image.mimeType, 'image/jpeg');
  assert.ok(Math.max(cap.image.width, cap.image.height) <= 2560);
  assert.equal(cap.image.displayId, undefined);
});
test('capture flow: bad / vanished / empty sources fail with clear errors', async () => {
  const { invoke } = fakeElectron({ sources: [{ ...SOURCES[0], empty: false }, { id: 'window:9:0', name: 'x', display_id: '', empty: true }] });
  assert.equal((await invoke('screenshot:capture', '../etc/passwd')).error.code, 'BAD_SOURCE');
  assert.equal((await invoke('screenshot:capture', 'window:404:0')).error.code, 'SOURCE_GONE');
  assert.equal((await invoke('screenshot:capture', 'window:9:0')).error.code, 'EMPTY_CAPTURE');
});
test('capture flash: border window is hidden from screen share', () => {
  const calls = [];
  function BrowserWindow() {
    return {
      setIgnoreMouseEvents: () => {},
      setContentProtection: (on) => calls.push(on),
      loadURL: () => {},
      once: () => {},
      isDestroyed: () => true,
      close: () => {},
    };
  }
  const screen = {
    getAllDisplays: () => [{ id: 1, bounds: { x: 0, y: 0, width: 100, height: 100 } }],
    getPrimaryDisplay: () => ({ bounds: { x: 0, y: 0, width: 100, height: 100 } }),
  };
  createCapture({ desktopCapturer: {}, screen, BrowserWindow }).flashDisplay('1');
  assert.deepEqual(calls, [true]);
});
test('ipc: untrusted senders are rejected on every channel', async () => {
  const { invoke } = fakeElectron({ sources: SOURCES, trustedSender: false });
  for (const ch of ['screenshot:get-config', 'screenshot:list-sources', 'screenshot:capture', 'screenshot:analyze']) {
    assert.equal((await invoke(ch, 'x', 'y')).error.code, 'FORBIDDEN');
  }
});
test('ipc: no key channels exist and config reports server readiness, never a key', async () => {
  const { invoke } = fakeElectron({ sources: SOURCES });
  const handlers = {};
  registerScreenshotIpc({ ipcMain: { handle: (ch, fn) => (handlers[ch] = fn) }, getMainWindow: () => null, capture: {}, apiBase: API });
  assert.equal(handlers['screenshot:set-key'], undefined);
  assert.equal(handlers['screenshot:clear-key'], undefined);
  const cfg = await invoke('screenshot:get-config');
  assert.equal(cfg.serverReady, true);
  assert.equal(cfg.defaultModel, 'gemini-3.5-flash-lite');
  assert.equal(cfg.keyStored, undefined);
});
test('ipc: analyze goes to the backend and returns the result', async () => {
  const { invoke, calls } = fakeElectron({ sources: SOURCES });
  const ok = await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, [img(500)]);
  assert.equal(ok.ok, true);
  assert.equal(ok.model, 'Gemini 3.5 Flash Lite');
  assert.deepEqual(calls, [`${API}/api/screenshots/analyze`]);
  assert.equal((await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, [])).error.code, 'VALIDATION');
});

test('hotkey: Ctrl+Shift+S is app-wide — opens the Screenshots page elsewhere, the picker on that page', async () => {
  const handlers = {};
  const sent = [];
  const registered = {};
  const sender = { send: (...a) => sent.push(a), on() {}, once() {} };
  let focused = 0;
  const win = { isDestroyed: () => false, webContents: sender, focus: () => focused++ };
  const desktopCapturer = {
    getSources: async () => [
      { id: 'screen:1:0', display_id: '1' },
      { id: 'screen:2:0', display_id: '2' },
    ],
  };
  const screen = {
    getCursorScreenPoint: () => ({ x: 2000, y: 10 }),
    getDisplayNearestPoint: () => ({ id: 2 }),
  };
  const globalShortcut = {
    register: (combo, fn) => ((registered[combo] = fn), true),
    unregister: (combo) => delete registered[combo],
  };
  registerScreenshotIpc({
    ipcMain: { handle: (ch, fn) => (handlers[ch] = fn) },
    getMainWindow: () => win,
    globalShortcut,
    capture: createCapture({ desktopCapturer, screen, BrowserWindow() {} }),
    apiBase: API,
  });
  const press = registered['CommandOrControl+Shift+S'];
  assert.ok(press, 'registered app-wide at startup');

  // Not on the Screenshots page: bring the app forward and open the page.
  await press();
  assert.deepEqual(sent, [['screenshot:open-page']]);
  assert.equal(focused, 1);

  assert.equal((await handlers['screenshot:set-hotkey']({ sender: {} }, true)).error.code, 'FORBIDDEN');
  assert.equal((await handlers['screenshot:set-hotkey']({ sender }, true)).active, true);
  await press();
  assert.deepEqual(sent[1], ['screenshot:hotkey', 'screen:2:0']);
  assert.equal(focused, 2, 'window is focused so the picker receives arrow / Enter keys');

  // Page closed: back to "open the page", and the shortcut stays registered.
  assert.equal((await handlers['screenshot:set-hotkey']({ sender }, false)).active, false);
  assert.ok(registered['CommandOrControl+Shift+S']);
  await press();
  assert.deepEqual(sent[2], ['screenshot:open-page']);
});
