// fetch() that carries the app session's cookies, on every supported Electron.
//
// The desktop app ships Electron 22 so ONE installer runs on Windows 7 → 11
// (Electron 23+ dropped Windows 7/8/8.1). `session.fetch` only exists from
// Electron 25, so this falls back to `net.request` with the same session and
// returns the small part of the Response API the app uses (ok, status, text, json).

function abortError() {
  const err = new Error('The operation was aborted.');
  err.name = 'AbortError';
  return err;
}

/**
 * @param {{ net: any, ses: any }} deps
 * @returns {(url: string, init?: object) => Promise<{ ok: boolean, status: number, text(): Promise<string>, json(): Promise<any> }>}
 */
function createSessionFetch({ net, ses }) {
  if (typeof ses.fetch === 'function') {
    return (url, init = {}) => ses.fetch(url, { ...init, credentials: 'include' });
  }
  return (url, init = {}) =>
    new Promise((resolve, reject) => {
      const signal = init.signal;
      if (signal?.aborted) {
        reject(abortError());
        return;
      }
      const request = net.request({
        url,
        method: String(init.method || 'GET').toUpperCase(),
        session: ses,
        useSessionCookies: true,
      });
      for (const [name, value] of Object.entries(init.headers || {})) {
        request.setHeader(name, String(value));
      }
      let settled = false;
      const fail = (err) => {
        if (settled) return;
        settled = true;
        reject(err);
      };
      const onAbort = () => {
        try {
          request.abort();
        } catch {
          // Already finished — nothing to cancel.
        }
        fail(abortError());
      };
      signal?.addEventListener?.('abort', onAbort, { once: true });

      request.on('response', (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(Buffer.from(chunk)));
        response.on('end', () => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener?.('abort', onAbort);
          const body = Buffer.concat(chunks).toString('utf8');
          const status = response.statusCode;
          resolve({
            ok: status >= 200 && status < 300,
            status,
            text: async () => body,
            json: async () => JSON.parse(body),
          });
        });
        response.on('error', fail);
      });
      request.on('error', fail);
      if (init.body != null) request.write(init.body);
      request.end();
    });
}

module.exports = { createSessionFetch };
