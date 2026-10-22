// One-time cleanup of AI keys that older desktop builds stored on the PC.
// Analysis now runs on the backend with the server's key, so the encrypted
// key store (ai-keys.json) and any key lines in <userData>/.env are removed.
// Only those key entries are touched; other .env lines are preserved.
const path = require('path');

const KEY_STORE_FILE = 'ai-keys.json';
const KEY_LINE = /^\s*(?:export\s+)?(?:GEMINI_API_KEY|GOOGLE_API_KEY)\s*=/;

/** @returns {{ removedKeyStore: boolean, scrubbedEnv: boolean }} */
function purgeLegacyKeys({ fs, userDataDir }) {
  const result = { removedKeyStore: false, scrubbedEnv: false };

  try {
    fs.unlinkSync(path.join(userDataDir, KEY_STORE_FILE));
    result.removedKeyStore = true;
  } catch {
    // Not present (fresh install) or already removed.
  }

  const envFile = path.join(userDataDir, '.env');
  try {
    const lines = fs.readFileSync(envFile, 'utf8').split(/\r?\n/);
    const kept = lines.filter((l) => !KEY_LINE.test(l));
    if (kept.length !== lines.length) {
      if (kept.every((l) => !l.trim())) fs.unlinkSync(envFile);
      else fs.writeFileSync(envFile, kept.join('\n'));
      result.scrubbedEnv = true;
    }
  } catch {
    // No .env, or unreadable: nothing to scrub.
  }
  return result;
}

module.exports = { purgeLegacyKeys, KEY_STORE_FILE };
