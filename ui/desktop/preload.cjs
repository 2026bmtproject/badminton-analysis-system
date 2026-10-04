const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("badmintonDesktop", Object.freeze({
  status: () => ipcRenderer.invoke("desktop:status"),
  chooseMatches: () => ipcRenderer.invoke("desktop:choose-matches"),
  chooseBackend: () => ipcRenderer.invoke("desktop:choose-backend"),
  chooseUv: () => ipcRenderer.invoke("desktop:choose-uv"),
}));
