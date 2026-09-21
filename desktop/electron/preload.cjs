const { contextBridge, ipcRenderer, webFrame } = require("electron");

contextBridge.exposeInMainWorld("cureflow", {
  apiBase: "http://127.0.0.1:8766",
  platform: process.platform,
  selectDataFile: () => ipcRenderer.invoke("cureflow:select-data-file"),
  // Backward-compatible alias for older renderer builds.
  selectCsv: () => ipcRenderer.invoke("cureflow:select-csv"),
  exportReport: (payload) => ipcRenderer.invoke("cureflow:export-report", payload),
  saveDataFile: (payload) => ipcRenderer.invoke("cureflow:save-data-file", payload),
  configureProvider: (payload) => ipcRenderer.invoke("cureflow:configure-provider", payload),
  setUiScale: (value) => {
    const factor = Number(value);
    if (!Number.isFinite(factor) || factor < 0.85 || factor > 1.35) {
      throw new Error("界面缩放必须位于 85% 到 135% 之间");
    }
    webFrame.setZoomFactor(factor);
    return factor;
  },
  onBackendHealth: (callback) => {
    const listener = (_event, healthy) => callback(Boolean(healthy));
    ipcRenderer.on("cureflow:backend-health", listener);
    return () => ipcRenderer.removeListener("cureflow:backend-health", listener);
  },
});
