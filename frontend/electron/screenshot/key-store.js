// Encrypted API-key storage. Keys are encrypted with Electron `safeStorage`
// (Windows DPAPI / macOS Keychain) and written to the user-data directory.
// There is deliberately NO plaintext fallback, and no method returns the key
// to the renderer — only the main process reads it, to make the request.
const path = require('path');

const FILE_NAME = 'ai-keys.json';

function createKeyStore({ safeStorage, fs, userDataDir }) {
  const file = path.join(userDataDir, FILE_NAME);

  function readAll() {
    try {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }

  function writeAll(obj) {
    fs.mkdirSync(userDataDir, { recursive: true });
    fs.writeFileSync(file, JSON.stringify(obj), { encoding: 'utf8', mode: 0o600 });
  }

  const encryptionAvailable = () => {
    try {
      return !!safeStorage?.isEncryptionAvailable();
    } catch {
      return false;
    }
  };

  return {
    encryptionAvailable,
    has(provider) {
      return typeof readAll()[provider] === 'string';
    },
    set(provider, apiKey) {
      const key = typeof apiKey === 'string' ? apiKey.trim() : '';
      if (key.length < 8 || /\s/.test(key)) {
        throw Object.assign(new Error('That does not look like a valid API key.'), {
          code: 'INVALID_KEY_FORMAT',
        });
      }
      if (!encryptionAvailable()) {
        throw Object.assign(
          new Error('Secure storage is not available on this system, so the key was not saved.'),
          { code: 'ENCRYPTION_UNAVAILABLE' },
        );
      }
      const all = readAll();
      all[provider] = safeStorage.encryptString(key).toString('base64');
      writeAll(all);
    },
    /** Main-process only. Returns null when absent or undecryptable. */
    get(provider) {
      const blob = readAll()[provider];
      if (typeof blob !== 'string' || !encryptionAvailable()) return null;
      try {
        return safeStorage.decryptString(Buffer.from(blob, 'base64'));
      } catch {
        return null;
      }
    },
    clear(provider) {
      const all = readAll();
      delete all[provider];
      writeAll(all);
    },
  };
}

module.exports = { createKeyStore, FILE_NAME };
