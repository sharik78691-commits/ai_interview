// Screen capture for the desktop app.
//
// Consent model: nothing here captures on its own. The renderer must first list
// sources, the user must pick one in the UI, and only then is `captureSource`
// called. Every capture is followed by an on-screen flash on the captured
// display (see `flashDisplay`) so a capture is never silent.
const MAX_LONG_EDGE = 2560;
const JPEG_QUALITY = 85;
const THUMB = { width: 320, height: 180 };

function createCapture({ desktopCapturer, screen, BrowserWindow }) {
  /** @param {string[]} excludeIds media-source ids to hide (this app's own windows). */
  async function listSources(excludeIds = []) {
    const sources = await desktopCapturer.getSources({
      types: ['screen', 'window'],
      thumbnailSize: THUMB,
      fetchWindowIcons: false,
    });
    return sources
      .filter((s) => !excludeIds.includes(s.id) && s.name && !s.thumbnail.isEmpty())
      .map((s) => ({
        id: s.id,
        name: s.name,
        kind: s.id.startsWith('screen:') ? 'screen' : 'window',
        thumbnail: s.thumbnail.toDataURL(),
      }));
  }

  function sizeFor(source) {
    const display = screen
      .getAllDisplays()
      .find((d) => String(d.id) === String(source.display_id));
    if (display) {
      return {
        width: Math.round(display.size.width * (display.scaleFactor || 1)),
        height: Math.round(display.size.height * (display.scaleFactor || 1)),
      };
    }
    return { width: 3840, height: 2160 };
  }

  async function captureSource(sourceId) {
    if (typeof sourceId !== 'string' || !/^(screen|window):/.test(sourceId)) {
      throw Object.assign(new Error('Invalid capture source.'), { code: 'BAD_SOURCE' });
    }
    // First pass (cheap) finds the source so we can size the real capture.
    const probe = await desktopCapturer.getSources({
      types: ['screen', 'window'],
      thumbnailSize: { width: 1, height: 1 },
    });
    const match = probe.find((s) => s.id === sourceId);
    if (!match) {
      throw Object.assign(new Error('That screen or window is no longer available.'), {
        code: 'SOURCE_GONE',
      });
    }
    const full = await desktopCapturer.getSources({
      types: [sourceId.startsWith('screen:') ? 'screen' : 'window'],
      thumbnailSize: sizeFor(match),
    });
    const src = full.find((s) => s.id === sourceId);
    let image = src?.thumbnail;
    if (!image || image.isEmpty()) {
      throw Object.assign(new Error('The capture came back empty. Try another source.'), {
        code: 'EMPTY_CAPTURE',
      });
    }
    let { width, height } = image.getSize();
    const longEdge = Math.max(width, height);
    if (longEdge > MAX_LONG_EDGE) {
      const scale = MAX_LONG_EDGE / longEdge;
      image = image.resize({
        width: Math.round(width * scale),
        height: Math.round(height * scale),
        quality: 'best',
      });
      ({ width, height } = image.getSize());
    }
    return {
      mimeType: 'image/jpeg',
      data: image.toJPEG(JPEG_QUALITY).toString('base64'),
      width,
      height,
      sourceName: src.name,
      displayId: match.display_id || '',
    };
  }

  /** Brief border flash on the captured display — the visible "a capture happened" cue. */
  function flashDisplay(displayId) {
    try {
      const display =
        screen.getAllDisplays().find((d) => String(d.id) === String(displayId)) ||
        screen.getPrimaryDisplay();
      const win = new BrowserWindow({
        ...display.bounds,
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        skipTaskbar: true,
        focusable: false,
        resizable: false,
        hasShadow: false,
        show: false,
      });
      win.setIgnoreMouseEvents(true);
      const html =
        '<body style="margin:0;background:transparent"><div style="position:fixed;inset:0;' +
        'box-sizing:border-box;border:6px solid #4f8cff;box-shadow:inset 0 0 40px #4f8cff;' +
        'animation:f .7s ease-out forwards"></div><style>@keyframes f{to{opacity:0}}</style></body>';
      win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
      win.once('ready-to-show', () => win.showInactive());
      setTimeout(() => {
        if (!win.isDestroyed()) win.close();
      }, 800);
    } catch {
      // The flash is best-effort; the in-app "captured" indicator still shows.
    }
  }

  return { listSources, captureSource, flashDisplay };
}

module.exports = { createCapture, MAX_LONG_EDGE };
