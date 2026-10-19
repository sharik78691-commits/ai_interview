// Run with: npm run test:electron
const test = require('node:test');
const assert = require('node:assert/strict');
const { createLoopbackAudioHandler } = require('../system-audio');

const screen = { id: 'screen:0:0', name: 'Entire screen' };
const fakeCapturer = (sources) => ({ getSources: async () => sources });
const run = (handler, request) =>
  new Promise((resolve) => handler(request, (result) => resolve(result)));

test('Auto Select on Windows gets screen + loopback audio with no picker', async () => {
  const handler = createLoopbackAudioHandler({ desktopCapturer: fakeCapturer([screen]), platform: 'win32' });
  assert.deepEqual(await run(handler, { audioRequested: true, videoRequested: true }), {
    video: screen,
    audio: 'loopback',
  });
});

test('requests without audio are denied', async () => {
  const handler = createLoopbackAudioHandler({ desktopCapturer: fakeCapturer([screen]), platform: 'win32' });
  assert.deepEqual(await run(handler, { audioRequested: false, videoRequested: true }), {});
});

test('no loopback audio outside Windows (renderer falls back to a device)', async () => {
  const handler = createLoopbackAudioHandler({ desktopCapturer: fakeCapturer([screen]), platform: 'darwin' });
  assert.deepEqual(await run(handler, { audioRequested: true }), { video: screen });
});

test('no screen source or a capturer failure denies instead of hanging', async () => {
  const empty = createLoopbackAudioHandler({ desktopCapturer: fakeCapturer([]), platform: 'win32' });
  assert.deepEqual(await run(empty, { audioRequested: true }), {});
  const failing = createLoopbackAudioHandler({
    desktopCapturer: { getSources: async () => { throw new Error('x'); } },
    platform: 'win32',
  });
  assert.deepEqual(await run(failing, { audioRequested: true }), {});
});
