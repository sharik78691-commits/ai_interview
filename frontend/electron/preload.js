// Preload for the MAIN window — exposes a safe, minimal bridge to the Angular
// app. contextIsolation is on, so this is the only way the page reaches Node.
const { contextBridge, ipcRenderer } = require('electron');

// Branding: Task Manager and the taskbar show the WINDOW TITLE, and the
// Angular app rewrites document.title on every route (SEO titles containing
// the old website name). Pin it to the product name so only it ever appears.
const BRAND_TITLE = 'audio O control';
const lockTitle = () => {
  try {
    if (document.title !== BRAND_TITLE) document.title = BRAND_TITLE;
  } catch {
    // Document not ready yet — startLock() below retries on DOM changes.
  }
};
const startLock = () => {
  try {
    if (!document.documentElement) return;
    new MutationObserver(lockTitle).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    lockTitle();
  } catch {
    // Still too early — DOMContentLoaded will retry.
  }
};
if (typeof document !== 'undefined' && document.readyState !== 'loading') {
  startLock();
} else if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', startLock, { once: true });
}

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  invisible: {
    start: () => ipcRenderer.invoke('copilot:start'),
    stop: () => ipcRenderer.invoke('copilot:stop'),
    update: (payload) => ipcRenderer.send('copilot:update', payload),
    onClosed: (cb) => ipcRenderer.on('copilot:closed', () => cb()),
  },
  setOpacity: (opacity) => ipcRenderer.invoke('window:set-opacity', opacity),
});
