// Run with: npm run test:electron   (node --test, no extra dependencies)
const test = require('node:test');
const assert = require('node:assert/strict');
const os = require('os');
const path = require('path');
const fs = require('fs');

const { DEFAULT_MODEL_KEY, listModels, resolveModel } = require('../model-config');
const { validateImages, base64Bytes } = require('../image-validation');
const { createKeyStore } = require('../key-store');
const { analyzeScreenshots, redact } = require('../analysis');
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

// ---- secure key storage ----
function fakeSafeStorage(available = true) {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (s) => Buffer.from('enc:' + [...s].reverse().join('')),
    decryptString: (b) => [...b.toString().slice(4)].reverse().join(''),
  };
}
test('key store: encrypts at rest, never writes plaintext, round-trips, clears', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'keys-'));
  const store = createKeyStore({ safeStorage: fakeSafeStorage(), fs, userDataDir: dir });
  store.set('google', 'AIzaSECRET_KEY_123456');
  const onDisk = fs.readFileSync(path.join(dir, 'ai-keys.json'), 'utf8');
  assert.ok(!onDisk.includes('AIzaSECRET_KEY_123456'));
  assert.equal(store.has('google'), true);
  assert.equal(store.get('google'), 'AIzaSECRET_KEY_123456');
  store.clear('google');
  assert.equal(store.has('google'), false);
});
test('key store: refuses to store without OS encryption or with a bad key', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'keys-'));
  const noEnc = createKeyStore({ safeStorage: fakeSafeStorage(false), fs, userDataDir: dir });
  assert.throws(() => noEnc.set('google', 'AIzaSECRET_KEY_123456'), { code: 'ENCRYPTION_UNAVAILABLE' });
  assert.equal(fs.existsSync(path.join(dir, 'ai-keys.json')), false);
  const ok = createKeyStore({ safeStorage: fakeSafeStorage(), fs, userDataDir: dir });
  assert.throws(() => ok.set('google', 'short'), { code: 'INVALID_KEY_FORMAT' });
});

// ---- AI request + error states ----
const model = () => resolveModel(DEFAULT_MODEL_KEY);
const goodBody = {
  candidates: [
    {
      content: {
        parts: [
          {
            text: JSON.stringify({
              questions: ['What is a closure?'],
              topic: 'JavaScript',
              seniority: 'Mid',
              directAnswer: 'A function plus its lexical scope.',
              interviewAnswer: 'A closure is...',
              keyPoints: ['lexical scope'],
              followUps: [{ question: 'Memory leaks?', suggestedResponse: 'Yes if...' }],
              code: null,
            }),
          },
        ],
      },
    },
  ],
};
const fakeFetch = (status, body) => async () => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
});
const run = (fetchImpl, apiKey = 'AIzaSECRET_KEY_123456') =>
  analyzeScreenshots({ model: model(), images: [img(500)], apiKey, fetchImpl });

test('analysis: success sends key in header (not URL), images inline, returns normalized result', async () => {
  let seen;
  const result = await run(async (url, opts) => {
    seen = { url, opts };
    return fakeFetch(200, goodBody)();
  });
  assert.ok(!seen.url.includes('AIza'));
  assert.equal(seen.opts.headers['x-goog-api-key'], 'AIzaSECRET_KEY_123456');
  assert.ok(seen.url.includes('gemini-3.5-flash-lite'));
  const body = JSON.parse(seen.opts.body);
  assert.equal(body.contents[0].parts.filter((p) => p.inline_data).length, 1);
  assert.equal(result.topic, 'JavaScript');
  assert.equal(result.code, null);
  assert.equal(result.followUps.length, 1);
});
test('analysis: error states map to clear codes', async () => {
  const code = async (p) => p.then(() => 'ok', (e) => e.code);
  assert.equal(await code(run(fakeFetch(200, goodBody), null)), 'NO_KEY');
  assert.equal(await code(run(fakeFetch(403, 'nope'))), 'INVALID_KEY');
  assert.equal(await code(run(fakeFetch(429, 'quota'))), 'RATE_LIMITED');
  assert.equal(await code(run(fakeFetch(413, ''))), 'TOO_LARGE');
  assert.equal(await code(run(fakeFetch(503, ''))), 'PROVIDER_ERROR');
  assert.equal(await code(run(fakeFetch(200, { candidates: [{ content: { parts: [{ text: 'not json' }] } }] }))), 'BAD_RESPONSE');
  assert.equal(await code(run(fakeFetch(200, { promptFeedback: { blockReason: 'SAFETY' } }))), 'BLOCKED');
  assert.equal(await code(run(async () => { throw new TypeError('fetch failed'); })), 'NETWORK');
  assert.equal(
    await code(analyzeScreenshots({ model: model(), images: [], apiKey: 'AIzaSECRET_KEY_123456', fetchImpl: fakeFetch(200, goodBody) })),
    'VALIDATION',
  );
  const slow = (_u, { signal }) =>
    new Promise((_r, rej) => signal.addEventListener('abort', () => rej(Object.assign(new Error('a'), { name: 'AbortError' }))));
  assert.equal(
    await code(analyzeScreenshots({ model: model(), images: [img(500)], apiKey: 'AIzaSECRET_KEY_123456', fetchImpl: slow, timeoutMs: 20 })),
    'TIMEOUT',
  );
});
test('analysis: key is redacted from provider error text', () => {
  assert.equal(redact('bad key AIzaSECRET_KEY_123456 here', 'AIzaSECRET_KEY_123456'), 'bad key [redacted] here');
  assert.ok(!redact('AIza' + 'x'.repeat(30), null).includes('xxxx'));
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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'keys-'));
  const keyStore = createKeyStore({ safeStorage: fakeSafeStorage(), fs, userDataDir: dir });
  registerScreenshotIpc({
    ipcMain: { handle: (ch, fn) => (handlers[ch] = fn), removeHandler() {} },
    getMainWindow: () => win,
    capture,
    keyStore,
    fetchImpl: fakeFetch(200, goodBody),
  });
  const invoke = (ch, ...args) => handlers[ch]({ sender: trustedSender ? sender : {} }, ...args);
  return { invoke, keyStore };
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
test('ipc: untrusted senders are rejected on every channel', async () => {
  const { invoke } = fakeElectron({ sources: SOURCES, trustedSender: false });
  for (const ch of ['screenshot:get-config', 'screenshot:list-sources', 'screenshot:capture', 'screenshot:set-key', 'screenshot:analyze']) {
    assert.equal((await invoke(ch, 'x', 'y')).error.code, 'FORBIDDEN');
  }
});
test('ipc: config exposes default model + key presence but never the key', async () => {
  const { invoke } = fakeElectron({ sources: SOURCES });
  assert.equal((await invoke('screenshot:get-config')).keyStored.google, false);
  assert.equal((await invoke('screenshot:set-key', DEFAULT_MODEL_KEY, 'AIzaSECRET_KEY_123456')).ok, true);
  const cfg = await invoke('screenshot:get-config');
  assert.equal(cfg.keyStored.google, true);
  assert.equal(cfg.defaultModel, 'gemini-3.5-flash-lite');
  assert.ok(!JSON.stringify(cfg).includes('AIzaSECRET'));
  assert.equal((await invoke('screenshot:set-key', 'nope', 'AIzaSECRET_KEY_123456')).error.code, 'UNKNOWN_MODEL');
});
test('ipc: analyze without a key reports NO_KEY; with a key returns the result', async () => {
  const { invoke } = fakeElectron({ sources: SOURCES });
  const images = [img(500)];
  assert.equal((await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, images)).error.code, 'NO_KEY');
  await invoke('screenshot:set-key', DEFAULT_MODEL_KEY, 'AIzaSECRET_KEY_123456');
  const ok = await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, images);
  assert.equal(ok.ok, true);
  assert.equal(ok.model, 'Gemini 3.5 Flash Lite');
  assert.equal((await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, [])).error.code, 'VALIDATION');
});

// ---- automatic key from .env ----
const { parseDotenv, createEnvKeys } = require('../env-key');

test('env-key: parses .env syntax and prefers process env over files', () => {
  assert.deepEqual(
    parseDotenv('# c\nGEMINI_API_KEY="AIzaFromFile12345" # x\nexport OTHER=1\nBAD LINE\n'),
    { GEMINI_API_KEY: 'AIzaFromFile12345', OTHER: '1' },
  );
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'env-'));
  const file = path.join(dir, '.env');
  fs.writeFileSync(file, 'GEMINI_API_KEY=AIzaFromFile12345\n');
  const keys = createEnvKeys({ fs, candidates: [path.join(dir, 'missing.env'), file], env: {} });
  assert.equal(keys.get('google'), 'AIzaFromFile12345');
  assert.equal(createEnvKeys({ fs, candidates: [file], env: { GEMINI_API_KEY: 'AIzaFromEnv123456' } }).get('google'), 'AIzaFromEnv123456');
  assert.equal(createEnvKeys({ fs, candidates: [], env: {} }).has('google'), false);
});

test('ipc: .env key is used automatically; saved key overrides; key never reaches renderer', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'env-'));
  const file = path.join(dir, '.env');
  fs.writeFileSync(file, 'GEMINI_API_KEY=AIzaFromEnvFile1234\n');
  const handlers = {};
  const sender = {};
  const win = { isDestroyed: () => false, webContents: sender, getMediaSourceId: () => 'x' };
  const keyStore = createKeyStore({ safeStorage: fakeSafeStorage(), fs, userDataDir: dir });
  const seenKeys = [];
  registerScreenshotIpc({
    ipcMain: { handle: (ch, fn) => (handlers[ch] = fn) },
    getMainWindow: () => win,
    capture: {},
    keyStore,
    envKeys: createEnvKeys({ fs, candidates: [file], env: {} }),
    envHint: path.join(dir, '.env'),
    fetchImpl: async (_u, opts) => {
      seenKeys.push(opts.headers['x-goog-api-key']);
      return fakeFetch(200, goodBody)();
    },
  });
  const invoke = (ch, ...a) => handlers[ch]({ sender }, ...a);
  const cfg = await invoke('screenshot:get-config');
  assert.equal(cfg.keyStored.google, true);
  assert.equal(cfg.keySources.google, 'env');
  assert.ok(!JSON.stringify(cfg).includes('AIzaFromEnvFile'));
  assert.equal((await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, [img(500)])).ok, true);
  await invoke('screenshot:set-key', DEFAULT_MODEL_KEY, 'AIzaSavedKey1234567');
  assert.equal((await invoke('screenshot:get-config')).keySources.google, 'stored');
  await invoke('screenshot:analyze', DEFAULT_MODEL_KEY, [img(500)]);
  assert.deepEqual(seenKeys, ['AIzaFromEnvFile1234', 'AIzaSavedKey1234567']);
  await invoke('screenshot:clear-key', DEFAULT_MODEL_KEY);
  assert.equal((await invoke('screenshot:get-config')).keySources.google, 'env');
});
