// Preload for the COPILOT window — receives transcript/guidance updates from
// the main process, can pull the current state on load, and can close itself.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('copilotAPI', {
  onUpdate: (cb) => ipcRenderer.on('copilot:update', (_event, payload) => cb(payload)),
  getState: () => ipcRenderer.invoke('copilot:get-state'),
  close: () => ipcRenderer.send('copilot:close'),
});
