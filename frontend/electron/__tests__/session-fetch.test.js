// Run with: npm run test:electron
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createSessionFetch } = require('../session-fetch');

// Fake `net.request` (Electron 22 has no session.fetch).
function fakeNet({ status = 200, body = '' } = {}) {
  const calls = [];
  return {
    calls,
    request(opts) {
      const req = new EventEmitter();
      const call = { opts, headers: {}, written: '', aborted: false };
      calls.push(call);
      req.setHeader = (k, v) => (call.headers[k] = v);
      req.write = (b) => (call.written += b);
      req.abort = () => (call.aborted = true);
      req.end = () =>
        setImmediate(() => {
          if (call.aborted) return;
          const res = new EventEmitter();
          res.statusCode = status;
          req.emit('response', res);
          res.emit('data', Buffer.from(body));
          res.emit('end');
        });
      return req;
    },
  };
}

test('uses session.fetch when Electron has it', async () => {
  const ses = { fetch: async (url, init) => ({ url, init }) };
  const out = await createSessionFetch({ net: null, ses })('https://x/api', { method: 'POST' });
  assert.equal(out.init.credentials, 'include');
});

test('Electron 22: falls back to net.request with the session cookies', async () => {
  const net = fakeNet({ status: 200, body: '{"ok":true}' });
  const ses = {};
  const res = await createSessionFetch({ net, ses })('https://x/api', {
    method: 'post',
    headers: { 'X-CSRF-Token': 't' },
    body: '{"a":1}',
  });
  assert.equal(res.ok, true);
  assert.deepEqual(await res.json(), { ok: true });
  const [call] = net.calls;
  assert.equal(call.opts.method, 'POST');
  assert.equal(call.opts.session, ses);
  assert.equal(call.opts.useSessionCookies, true);
  assert.equal(call.headers['X-CSRF-Token'], 't');
  assert.equal(call.written, '{"a":1}');
});

test('Electron 22: error status and abort behave like fetch', async () => {
  const res = await createSessionFetch({ net: fakeNet({ status: 401, body: 'no' }), ses: {} })('https://x');
  assert.equal(res.ok, false);
  assert.equal(res.status, 401);
  assert.equal(await res.text(), 'no');

  const controller = new AbortController();
  const net = fakeNet();
  const pending = createSessionFetch({ net, ses: {} })('https://x', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(net.calls[0].aborted, true);
});
