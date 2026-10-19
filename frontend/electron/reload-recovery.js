// Reload recovery for the packaged app (loaded from file://).
//
// Angular's hash router resolves '#/route' against <base href="./">, so the
// address becomes …/browser/#/route — the app FOLDER, without index.html.
// Reloading (Ctrl+R / F5 / location.reload) that file:// folder fails and left
// the window blank. The main process uses this to spot that failed load and
// load index.html again on the same route.
const path = require('path');

const DEFAULT_HASH = '/interview';

/**
 * Hash route to restore when `url` is a failed load of the folder holding
 * `indexHtml`; null for any other URL (so normal failures are left alone and
 * a failure of index.html itself can never loop).
 */
function recoverableFolderHash(url, indexHtml) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'file:') return null;
    // file:///C:/x/y/ -> C:/x/y/ on Windows; /x/y/ stays as-is elsewhere.
    const target = path.normalize(decodeURIComponent(parsed.pathname).replace(/^\/(?=[a-zA-Z]:)/, ''));
    const trim = (p) => p.replace(/[\\/]+$/, '');
    const sameDir =
      process.platform === 'win32' || /^[a-zA-Z]:/.test(target)
        ? trim(target).toLowerCase() === trim(path.dirname(indexHtml)).toLowerCase()
        : trim(target) === trim(path.dirname(indexHtml));
    if (!sameDir) return null;
    const hash = parsed.hash.replace(/^#/, '');
    return hash.startsWith('/') ? hash : DEFAULT_HASH;
  } catch {
    return null;
  }
}

module.exports = { recoverableFolderHash, DEFAULT_HASH };
