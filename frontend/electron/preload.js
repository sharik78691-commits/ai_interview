// Preload for the MAIN window — exposes a safe, minimal bridge to the Angular
// app. contextIsolation is on, so this is the only way the page reaches Node.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  invisible: {
    start: () => ipcRenderer.invoke('copilot:start'),
    stop: () => ipcRenderer.invoke('copilot:stop'),
    update: (payload) => ipcRenderer.send('copilot:update', payload),
    onClosed: (cb) => ipcRenderer.on('copilot:closed', () => cb()),
  },
});
