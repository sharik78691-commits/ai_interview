const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { purgeLegacyKeys, KEY_STORE_FILE } = require('../legacy-key-cleanup');

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'legacy-keys-'));

test('removes the old encrypted key store', () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, KEY_STORE_FILE), '{"google":"x"}');
  const r = purgeLegacyKeys({ fs, userDataDir: dir });
  assert.equal(r.removedKeyStore, true);
  assert.equal(fs.existsSync(path.join(dir, KEY_STORE_FILE)), false);
});

test('scrubs only key lines from .env and keeps the rest', () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, '.env'), 'FOO=1\nGEMINI_API_KEY=AIzaSECRET\nexport GOOGLE_API_KEY="y"\nBAR=2');
  const r = purgeLegacyKeys({ fs, userDataDir: dir });
  assert.equal(r.scrubbedEnv, true);
  assert.equal(fs.readFileSync(path.join(dir, '.env'), 'utf8'), 'FOO=1\nBAR=2');
});

test('deletes a .env that only held keys; no-op on a clean profile', () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, '.env'), 'GEMINI_API_KEY=abc\n');
  purgeLegacyKeys({ fs, userDataDir: dir });
  assert.equal(fs.existsSync(path.join(dir, '.env')), false);
  assert.deepEqual(purgeLegacyKeys({ fs, userDataDir: tmp() }), { removedKeyStore: false, scrubbedEnv: false });
});
