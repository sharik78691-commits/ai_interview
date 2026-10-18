// Reads provider API keys from the environment / .env files so the desktop app
// works without pasting a key. Main-process only: values are returned to the
// caller (the AI request) and never sent to the renderer or logged.
const PROVIDER_ENV = {
  google: ['GEMINI_API_KEY', 'GOOGLE_API_KEY'],
};

/** Minimal dotenv parser: KEY=value, optional quotes, # comments, `export `. */
function parseDotenv(text) {
  const out = {};
  for (const line of String(text ?? '').split(/\r?\n/)) {
    const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m || line.trim().startsWith('#')) continue;
    let value = m[2];
    const quoted = value.match(/^(['"])(.*)\1/);
    if (quoted) value = quoted[2];
    else value = value.replace(/\s+#.*$/, '');
    out[m[1]] = value;
  }
  return out;
}

/**
 * @param {{ fs: any, candidates: string[], env?: Record<string,string|undefined> }} deps
 *   `candidates` are .env paths in priority order. Files are re-read on each
 *   lookup so editing .env takes effect without rebuilding the app.
 */
function createEnvKeys({ fs, candidates, env = process.env }) {
  function get(provider) {
    const names = PROVIDER_ENV[provider] || [];
    for (const n of names) if (env[n] && env[n].trim()) return env[n].trim();
    for (const file of candidates) {
      let parsed;
      try {
        parsed = parseDotenv(fs.readFileSync(file, 'utf8'));
      } catch {
        continue;
      }
      for (const n of names) if (parsed[n] && parsed[n].trim()) return parsed[n].trim();
    }
    return null;
  }
  return { get, has: (provider) => get(provider) !== null };
}

module.exports = { PROVIDER_ENV, parseDotenv, createEnvKeys };
