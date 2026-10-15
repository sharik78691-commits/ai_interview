// Electron main process — wraps the Angular app and hosts the capture-protected
// "Copilot" window used by "Invisible mode in screen share".
//
// Dev:   run `ng serve`, then `npm run electron` (loads http://localhost:4200).
// Prod:  `npm run dist:win` / `dist:mac` builds a packaged app (electron-builder)
//        that loads the built files from dist/frontend/browser.
const { app, BrowserWindow, globalShortcut, ipcMain, screen, session, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');

// Known Windows issue: with GPU acceleration, a content-protected window can
// render BLACK on the local screen too (not just in captures). Software
// rendering avoids it; this app is mostly text, so the cost is negligible.
app.disableHardwareAcceleration();

const DEV_URL = process.env.ELECTRON_START_URL || 'http://localhost:4200';

let mainWindow = null;
let copilotWindow = null;
let tray = null;
let trayHintShown = false;
let isQuitting = false;
// Latest transcript + guidance, replayed to the copilot when it (re)opens so it
// is never left blank/stale.
let lastPayload = null;

const INDEX_HTML = path.join(__dirname, '..', 'dist', 'frontend', 'browser', 'index.html');

/** Frontend pages on the public site. /api/ must be allowed — that is the OAuth callback. */
function isOldWebsitePage(url) {
  try {
    const parsed = new URL(url);
    if (!/(^|\.)oyeinterview\.com$/i.test(parsed.hostname)) return false;
    return !parsed.pathname.startsWith('/api/');
  } catch {
    return false;
  }
}

function loadDesktopApp() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  // Land on the interview screen. The auth guard sends signed-out users to #/login.
  mainWindow.loadFile(INDEX_HTML, { hash: '/interview' });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 880,
    title: 'audio O control',
    backgroundColor: '#0b1020',
    // Tray-only app: no taskbar button. The window is reached through the
    // Notification Area (system tray) icon created in createTray() below.
    skipTaskbar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // The packaged app is loaded from file:// (no dev-server proxy). Disabling
      // webSecurity lets (a) Angular's ES-module bundle load from file:// and
      // (b) the renderer call the deployed backend cross-origin without CORS
      // errors. This window only loads local files + our own backend, so the
      // risk is minimal for this MVP. contextIsolation + nodeIntegration off
      // still apply.
      webSecurity: false,
      // Keep the WebSocket + guidance pipeline running while the window is
      // minimized, so the copilot stays live after the main window is hidden.
      backgroundThrottling: false,
    },
  });

  // "Whole app invisible by default": hide the MAIN window from screen capture
  // too (not just the copilot). The interviewer sees this app as black in
  // Zoom / Meet / Teams / OBS. Combined with disableHardwareAcceleration()
  // above, it still renders normally on the user's own screen.
  mainWindow.setContentProtection(true);

  // Tray-only app (belt and braces): the skipTaskbar constructor option alone
  // did not hide the taskbar button on this Electron/Windows build, so assert
  // it explicitly — and re-assert on every show in case anything resets it.
  mainWindow.setSkipTaskbar(true);
  mainWindow.on('show', () => {
    try {
      mainWindow.setSkipTaskbar(true);
    } catch {
      // Window gone mid-shutdown — safe to ignore.
    }
  });

  if (!app.isPackaged) {
    mainWindow.loadURL(DEV_URL);
  } else {
    loadDesktopApp();
  }

  // Google login finishes with a redirect to https://www.oyeinterview.com/dashboard.
  // That host is still serving the OLD frontend ("Select meeting audio", no
  // Auto Select button). Stay in this packaged UI instead.
  const bounceOffOldSite = (_event, url) => {
    if (isOldWebsitePage(url)) loadDesktopApp();
  };
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isOldWebsitePage(url)) return;
    event.preventDefault();
    loadDesktopApp();
  });
  mainWindow.webContents.on('will-redirect', (event, url) => {
    if (!isOldWebsitePage(url)) return;
    event.preventDefault();
    loadDesktopApp();
  });
  mainWindow.webContents.on('did-navigate', bounceOffOldSite);
  mainWindow.webContents.on('did-redirect-navigation', bounceOffOldSite);

  // Tray app: closing (X) or minimizing hides the window into the
  // Notification Area instead of quitting. The renderer keeps running while
  // hidden, so meeting capture keeps working. Quit via the tray menu.
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      if (!trayHintShown && tray && !tray.isDestroyed()) {
        trayHintShown = true;
        try {
          tray.displayBalloon({
            title: 'audio O control',
            content: 'Running in the system tray — right-click the icon to quit.',
          });
        } catch {
          // Balloons are best-effort (group policy can disable them).
        }
      }
    }
  });
  mainWindow.on('minimize', (event) => {
    // With skipTaskbar there is no taskbar button to restore from — a
    // minimized window would be stranded, so tuck it into the tray instead.
    event.preventDefault();
    mainWindow.hide();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function createCopilotWindow() {
  if (copilotWindow && !copilotWindow.isDestroyed()) {
    copilotWindow.focus();
    return copilotWindow;
  }

  copilotWindow = new BrowserWindow({
    width: 420,
    height: 620,
    minWidth: 320,
    minHeight: 360,
    frame: false,
    transparent: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: true,
    hasShadow: true,
    backgroundColor: '#0b1020',
    webPreferences: {
      preload: path.join(__dirname, 'copilot-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // THE key line: exclude this window from screen capture.
  //   Windows -> SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
  //   macOS   -> NSWindowSharingNone
  // Zoom / Meet / Teams / OBS see this window as black.
  copilotWindow.setContentProtection(true);

  // Float it near the bottom-right of the primary display.
  const { workArea } = screen.getPrimaryDisplay();
  copilotWindow.setPosition(
    workArea.x + workArea.width - 440,
    workArea.y + workArea.height - 640,
  );

  copilotWindow.loadFile(path.join(__dirname, 'copilot.html'));

  // Replay the latest data once the page is ready (fixes "reopened window is
  // empty / not live"). The page also pulls state via `copilot:get-state`.
  copilotWindow.webContents.on('did-finish-load', () => {
    if (lastPayload) copilotWindow?.webContents.send('copilot:update', lastPayload);
  });

  copilotWindow.on('closed', () => {
    copilotWindow = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('copilot:closed');
    }
  });

  return copilotWindow;
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.show();
  mainWindow.focus();
}

function toggleMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isVisible()) mainWindow.hide();
  else showMainWindow();
}

// System-wide hotkey: Ctrl+Shift+A shows/hides the app from anywhere — even
// mid-interview, without touching the mouse, taskbar or tray.
function registerToggleHotkey() {
  const combo = 'CommandOrControl+Shift+A';
  try {
    const ok = globalShortcut.register(combo, () => toggleMainWindow());
    if (!ok) {
      console.warn(
        `[hotkey] ${combo} is already taken by another app — toggle hotkey disabled.`,
      );
    }
  } catch (err) {
    console.warn('[hotkey] could not register toggle hotkey:', err?.message || err);
  }
}

// The app's ONLY visible presence: an icon in the Notification Area
// (system tray). Left-click toggles the window, right-click opens the menu.
function createTray() {
  // Professional tray mark, generated by electron/make-tray-icon.js.
  // Resolves both packed (…/resources/app.asar/electron) and dev
  // (…/frontend/electron) layouts — electron-builder ships electron/**/*.
  const iconPath = path.join(__dirname, 'tray-icon.ico');
  if (!fs.existsSync(iconPath)) {
    console.warn('[tray] icon not found, running without a tray icon:', iconPath);
    return;
  }
  tray = new Tray(nativeImage.createFromPath(iconPath));
  tray.setToolTip('audio O control — Ctrl+Shift+A to show or hide');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Open audio O control', click: () => showMainWindow() },
      {
        label: 'Hide',
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide();
        },
      },
      {
        label: 'Show / Hide (Ctrl+Shift+A)',
        click: () => toggleMainWindow(),
      },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on('click', () => toggleMainWindow());
}

// ---- IPC: main window (Angular) <-> main process <-> copilot window ----

ipcMain.handle('copilot:start', () => {
  createCopilotWindow();
  return true;
});

ipcMain.handle('copilot:stop', () => {
  copilotWindow?.close();
  copilotWindow = null;
  return true;
});

// The copilot window pulls the current state when it loads.
ipcMain.handle('copilot:get-state', () => lastPayload);

// Relay transcript + guidance from the main window to the copilot window.
ipcMain.on('copilot:update', (_event, payload) => {
  lastPayload = payload;
  if (copilotWindow && !copilotWindow.isDestroyed()) {
    copilotWindow.webContents.send('copilot:update', payload);
  }
});

ipcMain.on('copilot:close', () => {
  copilotWindow?.close();
});

// Transparency slider (top bar): 0% = solid window, 100% = fully see-through
// so the user can watch windows behind the app. Visual only — the window
// still receives mouse input.
ipcMain.handle('window:set-opacity', (_event, opacity) => {
  const value = Number(opacity);
  if (!Number.isFinite(value)) return false;
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  try {
    mainWindow.setOpacity(Math.min(1, Math.max(0, value)));
    return true;
  } catch {
    return false;
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  try {
    globalShortcut.unregisterAll();
  } catch {
    // Shortcuts already gone — safe to ignore during shutdown.
  }
  try {
    tray?.destroy();
  } catch {
    // Already gone — safe to ignore during shutdown.
  }
  tray = null;
});

app.whenReady().then(async () => {
  // A rebuilt .exe ships a new hashed bundle (main-*.js), but Chromium happily
  // serves the PREVIOUS bundle out of disk cache. That is why a fresh install
  // still showed the old UI — "Select meeting audio" instead of the new
  // "Auto Select Meeting Audio" button. This app is entirely local, so there is
  // nothing worth caching: always load the freshly shipped files.
  try {
    await session.defaultSession.clearCache();
  } catch {
    // First run / nothing cached yet — safe to ignore.
  }

  // The CSRF cookie lives on www.oyeinterview.com and is not readable from
  // file:// via document.cookie. Attach it to API calls so login-protected
  // actions (start interview, transcribe) are not rejected.
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ['https://*.oyeinterview.com/api/*', 'https://oyeinterview.com/api/*'] },
    (details, callback) => {
      const headers = details.requestHeaders;
      const already = headers['X-CSRF-Token'] || headers['x-csrf-token'];
      if (already) {
        callback({ requestHeaders: headers });
        return;
      }
      session.defaultSession.cookies
        .get({ name: 'aia_csrf' })
        .then((cookies) => {
          const csrf =
            cookies.find((c) => (c.domain || '').includes('oyeinterview.com')) || cookies[0];
          if (csrf && csrf.value) headers['X-CSRF-Token'] = csrf.value;
          callback({ requestHeaders: headers });
        })
        .catch(() => callback({ requestHeaders: headers }));
    },
  );

  // Grant microphone / system-audio (Stereo Mix) access — "Auto Select Meeting
  // Audio" needs getUserMedia to read the loopback device. Deny everything else.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(['media', 'audioCapture', 'videoCapture'].includes(permission));
  });

  createMainWindow();
  createTray();
  registerToggleHotkey();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
