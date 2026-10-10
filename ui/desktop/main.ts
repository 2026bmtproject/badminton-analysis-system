import { app, BrowserWindow, dialog, ipcMain, Menu, Tray } from "electron";
import { join, resolve } from "node:path";
import { startLocalHost, type LocalHost } from "../scripts/local-host";
import { desktopRuntime } from "../scripts/local-matches";
import { closeAction, canSwitchDirectory } from "./lifecycle";
import { defaultSettings, profileRoot, readSettings, saveSettings,
  validateSettings, type DesktopSettings } from "./settings";
import { LocalTaskService, type ServiceStatus } from "./service-manager";

if (!app.requestSingleInstanceLock()) app.quit();
else {
  let window: BrowserWindow | null = null;
  let tray: Tray | null = null;
  let host: LocalHost | null = null;
  let service: LocalTaskService | null = null;
  let settings: DesktopSettings;
  let error: string | null = null;
  let quitting = false;
  let connecting: Promise<unknown> | null = null;
  const userData = app.getPath("userData");
  const appRoot = app.getAppPath();

  function showWindow() {
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.show(); window.focus();
  }

  function profile() { return profileRoot(userData, settings.matchesDir || "unconfigured"); }

  async function bootHost() {
    const dataDir = profile();
    const context = desktopRuntime(appRoot, settings.matchesDir || join(dataDir, "no-matches"),
      settings.backendDir || appRoot, dataDir, settings.uvBinary);
    host = await startLocalHost(context, join(appRoot, "dist"), {
      pipelinePort: () => service?.status.connected ? service.servicePort : null,
      serviceOrigin: () => service?.serviceOrigin || host!.origin,
    });
    service = new LocalTaskService(settings, join(dataDir, "tasks"), userData, host.origin);
    const validation = await validateSettings(settings);
    error = validation;
    if (!validation) {
      try { await service.connect(); error = null; }
      catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    }
  }

  async function status() {
    if (service) await service.refresh();
    return { desktop: true, settings, dataDir: profile(), hostOrigin: host?.origin ?? null,
      service: service?.status ?? null, error };
  }

  async function reconnect() {
    if (connecting || !service || await validateSettings(settings)) return;
    connecting = service.connect().then(() => { error = null; }).catch(cause => {
      error = cause instanceof Error ? cause.message : String(cause);
    }).finally(() => { connecting = null; });
    await connecting;
  }

  async function applySettings(next: DesktopSettings) {
    if (service) {
      await service.refresh();
      if (!canSwitchDirectory(service.status)) throw new Error("分析執行中或狀態未確認，無法切換目錄。請先等待任務結束。");
      if (!await service.stopOwnedIfIdle()) throw new Error("服務狀態未確認，無法切換目錄。");
    }
    if (host) await host.close();
    settings = next;
    await saveSettings(userData, settings);
    await bootHost();
    await window?.loadURL(`${host!.origin}/matches`);
    return status();
  }

  function verifySender(event: Electron.IpcMainInvokeEvent) {
    if (!window || event.sender !== window.webContents ||
        !event.senderFrame || new URL(event.senderFrame.url).origin !== host?.origin)
      throw new Error("不允許此視窗使用桌面設定");
  }

  async function choose(kind: "matches" | "backend" | "uv") {
    if (!window) throw new Error("視窗尚未建立");
    const result = kind === "uv"
      ? await dialog.showOpenDialog(window, { properties: ["openFile"], filters: [
          { name: "uv executable", extensions: ["exe"] }] })
      : await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
    if (result.canceled || !result.filePaths[0]) return status();
    const path = resolve(result.filePaths[0]);
    const next = { ...settings,
      ...(kind === "matches" ? { matchesDir: path } :
        kind === "backend" ? { backendDir: path } : { uvBinary: path }) };
    return applySettings(next);
  }

  async function requestQuit() {
    if (quitting) return;
    if (service) await service.refresh();
    const state = service?.status ?? { connected: false, owned: false, port: null,
      health: null, error: null, warning: null } satisfies ServiceStatus;
    if (closeAction(state) === "background") {
      const choice = dialog.showMessageBoxSync(window!, {
        type: "info", title: "分析仍在背景執行",
        message: "分析正在執行，或尚無法確認 worker 狀態。關閉視窗後分析會繼續。",
        buttons: ["留在背景", "返回程式"], defaultId: 0, cancelId: 1,
      });
      if (choice === 0) window?.hide();
      else showWindow();
      return;
    }
    if (service && !await service.stopOwnedIfIdle()) return;
    quitting = true;
    await host?.close();
    app.quit();
  }

  app.on("second-instance", () => showWindow());
  app.whenReady().then(async () => {
    settings = await readSettings(userData,
      defaultSettings(app.isPackaged ? undefined : resolve(appRoot, "..")));
    await bootHost();
    ipcMain.handle("desktop:status", async event => { verifySender(event); return status(); });
    ipcMain.handle("desktop:choose-matches", async event => { verifySender(event); return choose("matches"); });
    ipcMain.handle("desktop:choose-backend", async event => { verifySender(event); return choose("backend"); });
    ipcMain.handle("desktop:choose-uv", async event => { verifySender(event); return choose("uv"); });
    // The default menu has nothing the app needs, but its accelerators (reload,
    // devtools, zoom) still work while it is hidden; Alt shows it.
    // The page draws its own 35px title bar (as VS Code does); Windows keeps its native caption buttons over it.
    window = new BrowserWindow({ width: 1280, height: 850, minWidth: 720, minHeight: 560,
      title: "Badminton Review", autoHideMenuBar: true, backgroundColor: "#1f1f1f", titleBarStyle: "hidden",
      titleBarOverlay: { color: "#181818", symbolColor: "#cccccc", height: 35 }, webPreferences: { preload: join(appRoot, "desktop", "preload.cjs"),
        contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: false } });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      if (!host || new URL(url).origin !== host.origin) event.preventDefault();
    });
    window.on("close", event => { if (!quitting) { event.preventDefault(); void requestQuit(); } });
    await window.loadURL(`${host!.origin}/matches`);
    tray = new Tray(join(appRoot, "desktop", "icon.png"));
    tray.setToolTip("Badminton Review");
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "開啟 Badminton Review", click: showWindow },
      { label: "退出", click: () => { void requestQuit(); } },
    ]));
    tray.on("double-click", showWindow);
    setInterval(async () => {
      if (quitting || !service) return;
      await service.refresh();
      if (!service.status.connected) await reconnect();
    }, 3000).unref();
  }).catch(cause => {
    dialog.showErrorBox("Badminton Review 無法啟動", cause instanceof Error ? cause.message : String(cause));
    app.quit();
  });
}
