const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const { randomBytes } = require("node:crypto");
const { readNativeDataFile } = require("./data-file.cjs");

// Isolate automated startup checks from the user's running window and history.
if (!app.isPackaged && process.env.CUREFLOW_SMOKE_USER_DATA) {
  app.setPath("userData", process.env.CUREFLOW_SMOKE_USER_DATA);
}

let backend = null;
let mainWindow = null;
let healthTimer = null;
let stderrLines = 0;
let stderrRemainder = "";
let stderrDialogShown = false;
let backendRestarting = false;
const sessionProviderEnv = Object.create(null);
const desktopKeyToken = randomBytes(32).toString("hex");
const DOUBAO_MODEL = "doubao-seed-2-0-pro-260215";
const backendPort = Number(process.env.CUREFLOW_BACKEND_PORT || 8766);
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error("本地服务端口无效");
const backendUrl = `http://127.0.0.1:${backendPort}`;
const STDERR_DIALOG_THRESHOLD = 20;
const PROVIDER_SESSION_CONFIG = Object.freeze({
  openai: {
    keyEnv: "OPENAI_API_KEY",
    confirmEnv: "CUREFLOW_ENABLE_OPENAI_LIVE",
  },
  qwen: {
    keyEnv: "QWEN_API_KEY",
    confirmEnv: "CUREFLOW_ENABLE_QWEN_LIVE",
  },
  deepseek: {
    keyEnv: "DEEPSEEK_API_KEY",
    confirmEnv: "CUREFLOW_ENABLE_DEEPSEEK_LIVE",
  },
  doubao: {
    keyEnv: "DOUBAO_API_KEY",
    confirmEnv: "CUREFLOW_ENABLE_DOUBAO_LIVE",
    modelEnv: "DOUBAO_MODEL",
  },
});

async function selectDataFile() {
  const result = await dialog.showOpenDialog({
    title: "选择表格数据",
    properties: ["openFile"],
    filters: [{ name: "表格数据", extensions: ["csv", "xlsx"] }],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  return readNativeDataFile(result.filePaths[0]);
}

ipcMain.handle("cureflow:select-data-file", selectDataFile);
// Kept for older renderers; both channels use the same guarded implementation.
ipcMain.handle("cureflow:select-csv", selectDataFile);

ipcMain.handle("cureflow:export-report", async (_event, payload) => {
  if (!payload || typeof payload.content !== "string") throw new Error("没有可导出的报告");
  const safeRunId = String(payload.runId || "report").replace(/[^A-Za-z0-9_-]/g, "_");
  const result = await dialog.showSaveDialog({
    title: "导出 CureFlow 报告",
    defaultPath: path.join(app.getPath("documents"), `CureFlow_${safeRunId}.md`),
    filters: [{ name: "Markdown 报告", extensions: ["md"] }],
  });
  if (result.canceled || !result.filePath) return { canceled: true };
  await fs.writeFile(result.filePath, payload.content, "utf8");
  return { canceled: false, filePath: result.filePath };
});

ipcMain.handle("cureflow:save-data-file", async (_event, payload) => {
  if (!payload || typeof payload.filename !== "string") throw new Error("没有可导出的修正版数据");
  const extension = path.extname(payload.filename).toLowerCase();
  if (![".csv", ".xlsx"].includes(extension)) throw new Error("修正版仅支持 CSV 或 XLSX");
  const safeName = path.basename(payload.filename).replace(/[<>:"/\\|?*]/g, "_");
  const result = await dialog.showSaveDialog({
    title: "保存 CureFlow 修正版",
    defaultPath: path.join(app.getPath("documents"), safeName),
    filters: [{ name: extension === ".xlsx" ? "Excel 工作簿" : "CSV 数据", extensions: [extension.slice(1)] }],
  });
  if (result.canceled || !result.filePath) return { canceled: true };
  if (extension === ".xlsx") {
    if (typeof payload.xlsxBase64 !== "string") throw new Error("修正版工作簿内容无效");
    await fs.writeFile(result.filePath, Buffer.from(payload.xlsxBase64, "base64"));
  } else {
    if (typeof payload.csvText !== "string") throw new Error("修正版 CSV 内容无效");
    await fs.writeFile(result.filePath, payload.csvText, "utf8");
  }
  return { canceled: false, filePath: result.filePath };
});

function projectRoot() {
  return path.resolve(__dirname, "..", "..");
}

function backendCommand(dataDir) {
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, "backend", "cureflow-backend.exe"),
      args: ["--port", String(backendPort), "--data-dir", dataDir],
      cwd: path.join(process.resourcesPath, "backend"),
    };
  }
  return {
    command: process.env.CUREFLOW_PYTHON || "python",
    args: [path.join(projectRoot(), "cureflow_app", "server.py"), "--port", String(backendPort), "--data-dir", dataDir],
    cwd: projectRoot(),
  };
}

function startBackend() {
  const dataDir = path.join(app.getPath("userData"), "runtime");
  const launch = backendCommand(dataDir);
  backend = spawn(launch.command, launch.args, {
    cwd: launch.cwd, windowsHide: true,
    // Electron explicitly restricts CORS: Vite in development, local file origin when packaged.
    env: {
      ...process.env,
      ...sessionProviderEnv,
      CUREFLOW_DESKTOP_KEY_TOKEN: desktopKeyToken,
      PYTHONUTF8: "1",
      CORS_ORIGINS: process.env.CUREFLOW_DEV_URL ? "http://127.0.0.1:5173" : "null",
    },
  });
  const logPath = path.join(app.getPath("userData"), "cureflow-backend.log");
  backend.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    process.stderr.write(text);
    fs.appendFile(logPath, text, "utf8").catch(() => {});
    const lines = (stderrRemainder + text).split(/\r?\n/);
    stderrRemainder = lines.pop() || "";
    stderrLines += lines.length;
    if (stderrLines >= STDERR_DIALOG_THRESHOLD && !stderrDialogShown) {
      stderrDialogShown = true;
      const options = {
        type: "error", title: "CureFlow 本地服务异常",
        message: "本地服务异常，请查看日志", detail: logPath,
      };
      if (mainWindow) dialog.showMessageBox(mainWindow, options);
      else dialog.showMessageBox(options);
    }
  });
  backend.on("error", (error) => {
    const hint = app.isPackaged
      ? "安装文件可能不完整，请重新安装 CureFlow"
      : "请确认 Python 与项目依赖已安装";
    dialog.showErrorBox("CureFlow 启动失败", `无法启动本地分析服务：${error.message}\n${hint}`);
  });
}

async function stopBackend() {
  const current = backend;
  backend = null;
  if (!current || current.killed || current.exitCode !== null) return;
  await new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    current.once("exit", finish);
    current.kill();
    setTimeout(finish, 2_000);
  });
}

async function restartBackend() {
  if (backendRestarting) throw new Error("本地引擎正在更新连接，请稍候");
  backendRestarting = true;
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("cureflow:backend-health", false);
    }
    await stopBackend();
    startBackend();
    await waitForBackend();
    await publishBackendHealth();
  } finally {
    backendRestarting = false;
  }
}

function assertTrustedRenderer(event) {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender.id !== mainWindow.webContents.id) {
    throw new Error("拒绝非主窗口的连接配置请求");
  }
}

ipcMain.handle("cureflow:configure-provider", async (event, payload) => {
  assertTrustedRenderer(event);
  const provider = String(payload?.provider || "").toLowerCase();
  const spec = PROVIDER_SESSION_CONFIG[provider];
  if (!spec) throw new Error("不支持的模型服务");
  const useExistingEnvironmentKey = payload?.useExistingEnvironmentKey === true;
  let modelId = typeof payload?.modelId === "string" ? payload.modelId.trim() : "";
  if (provider === "doubao") {
    modelId = modelId || String(sessionProviderEnv.DOUBAO_MODEL || process.env.DOUBAO_MODEL || "").trim();
    if (!modelId && useExistingEnvironmentKey) {
      const current = await fetch(`${backendUrl}/api/provider-status`).then(response => response.json());
      modelId = String(current.doubao?.model || "").trim();
    }
    modelId = modelId || DOUBAO_MODEL;
    if (!modelId || modelId.length > 128 || /\s/.test(modelId)) throw new Error("请填写已开通的火山方舟模型或接入点 ID");
  }
  if (useExistingEnvironmentKey) {
    const response = await fetch(`${backendUrl}/api/provider/configure`, {
      method: "POST",
      headers: { "X-CureFlow-Desktop-Token": desktopKeyToken, "Content-Type": "application/json" },
      body: JSON.stringify({ provider, useExistingEnvironmentKey: true, confirmSession: true, ...(provider === "doubao" ? { modelId } : {}) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "已有密钥无法启用");
    return result;
  }
  let apiKey = typeof payload?.apiKey === "string" ? payload.apiKey.trim() : "";
  {
    if (apiKey.length < 8 || apiKey.length > 4096 || /[\r\n\0]/.test(apiKey)) {
      throw new Error("API key 格式无效");
    }
    sessionProviderEnv[spec.keyEnv] = apiKey;
  }
  if (provider === "doubao") sessionProviderEnv.DOUBAO_MODEL = modelId;
  sessionProviderEnv[spec.confirmEnv] = "1";
  apiKey = "";
  try {
    await restartBackend();
    const response = await fetch(`${backendUrl}/api/provider-status`);
    if (!response.ok) throw new Error("连接状态检查失败");
    return await response.json();
  } catch (error) {
    if (!useExistingEnvironmentKey) delete sessionProviderEnv[spec.keyEnv];
    delete sessionProviderEnv[spec.confirmEnv];
    if (spec.modelEnv) delete sessionProviderEnv[spec.modelEnv];
    await restartBackend().catch(() => {});
    throw error;
  }
});

ipcMain.handle("cureflow:keys", async (event, payload) => {
  assertTrustedRenderer(event);
  const operation = payload?.operation;
  const provider = payload?.provider;
  const routes = {
    status: ["GET", "/api/keys/status"],
    optIn: ["POST", "/api/keys/opt-in"],
    optOut: ["POST", "/api/keys/opt-out"],
    set: ["POST", `/api/keys/${provider}`],
    delete: ["DELETE", `/api/keys/${provider}`],
  };
  if (!Object.hasOwn(routes, operation)) throw new Error("不支持的密钥管理操作");
  if (["set", "delete"].includes(operation) && !Object.hasOwn(PROVIDER_SESSION_CONFIG, provider)) {
    throw new Error("不支持的模型服务");
  }
  const [method, route] = routes[operation];
  let body;
  if (operation === "optIn" || operation === "optOut") body = { confirm: true };
  if (operation === "set") {
    const key = payload?.apiKey;
    if (typeof key !== "string" || key.length < 8 || key.length > 4096 || /\s/.test(key)) throw new Error("密钥格式无效");
    body = { apiKey: key };
    if (provider === "doubao") {
      const modelId = payload?.modelId || DOUBAO_MODEL;
      if (typeof modelId !== "string" || !modelId || modelId.length > 128 || /\s/.test(modelId)) throw new Error("请填写已开通的火山方舟模型或接入点 ID");
      body.modelId = modelId;
    }
  }
  const response = await fetch(`${backendUrl}${route}`, {
    method,
    headers: { "X-CureFlow-Desktop-Token": desktopKeyToken, ...(body ? { "Content-Type": "application/json" } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "本机密钥操作失败");
  return result;
});

ipcMain.handle("cureflow:probe-provider", async (event, provider) => {
  assertTrustedRenderer(event);
  if (!Object.hasOwn(PROVIDER_SESSION_CONFIG, provider)) throw new Error("不支持的模型服务");
  const response = await fetch(`${backendUrl}/api/provider/probe`, {
    method: "POST",
    headers: { "X-CureFlow-Desktop-Token": desktopKeyToken, "Content-Type": "application/json" },
    body: JSON.stringify({ provider, confirmProbe: true }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "模型连接验证失败");
  return result;
});

ipcMain.handle("cureflow:restart-backend", async (event) => {
  assertTrustedRenderer(event);
  await restartBackend();
  return { healthy: true };
});

async function waitForBackend() {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      if (backend?.exitCode !== null) throw new Error("本地服务提前退出");
      const response = await fetch(`${backendUrl}/api/keys/status`, { headers: { "X-CureFlow-Desktop-Token": desktopKeyToken } });
      if (response.ok || (response.status === 400 && (await response.json()).lastError)) return;
    } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("本地分析服务启动超时");
}

async function publishBackendHealth() {
  let healthy = false;
  try {
    const response = await fetch(`${backendUrl}/api/keys/status`, { headers: { "X-CureFlow-Desktop-Token": desktopKeyToken } });
    healthy = response.ok || (response.status === 400 && Boolean((await response.json()).lastError));
  } catch (_) {}
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("cureflow:backend-health", healthy);
  }
}

function startHealthMonitor() {
  publishBackendHealth();
  healthTimer = setInterval(publishBackendHealth, 3_000);
}

async function createWindow() {
  startBackend();
  const win = new BrowserWindow({
    width: 1440, height: 920, minWidth: 1100, minHeight: 720,
    backgroundColor: "#050b1a", title: "CureFlow",
    icon: path.join(__dirname, "..", app.isPackaged ? "dist" : "public", "cureflow-logo.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  mainWindow = win;
  win.removeMenu();
  if (process.env.CUREFLOW_DEV_URL) await win.loadURL(process.env.CUREFLOW_DEV_URL);
  else await win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  startHealthMonitor();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });
  app.whenReady().then(createWindow).catch((error) => {
    dialog.showErrorBox("CureFlow 启动失败", error.message); app.quit();
  });
}
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("before-quit", () => {
  if (healthTimer) clearInterval(healthTimer);
  if (backend && !backend.killed) backend.kill();
  for (const key of Object.keys(sessionProviderEnv)) delete sessionProviderEnv[key];
});
