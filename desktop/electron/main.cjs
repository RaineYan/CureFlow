const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

let backend = null;
let mainWindow = null;
let healthTimer = null;
let stderrLines = 0;
let stderrRemainder = "";
let stderrDialogShown = false;
let backendRestarting = false;
const sessionProviderEnv = Object.create(null);
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
});

async function selectDataFile() {
  const result = await dialog.showOpenDialog({
    title: "选择经营数据",
    properties: ["openFile"],
    filters: [{ name: "经营数据", extensions: ["csv", "xlsx"] }],
  });
  if (result.canceled || result.filePaths.length !== 1) return null;
  const filePath = result.filePaths[0];
  const stat = await fs.stat(filePath);
  if (stat.size > 5 * 1024 * 1024) throw new Error("数据文件不能超过 5 MB");
  const filename = path.basename(filePath);
  const extension = path.extname(filename).toLowerCase();
  if (extension === ".csv") {
    return { filename, csvText: await fs.readFile(filePath, "utf8") };
  }
  if (extension === ".xlsx") {
    const workbook = await fs.readFile(filePath);
    return { filename, xlsxBase64: workbook.toString("base64") };
  }
  throw new Error("仅支持 CSV 或 XLSX 文件");
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
      args: ["--port", "8766", "--data-dir", dataDir],
      cwd: path.join(process.resourcesPath, "backend"),
    };
  }
  return {
    command: process.env.CUREFLOW_PYTHON || "python",
    args: [path.join(projectRoot(), "cureflow_app", "server.py"), "--port", "8766", "--data-dir", dataDir],
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
  let apiKey = typeof payload?.apiKey === "string" ? payload.apiKey.trim() : "";
  if (useExistingEnvironmentKey) {
    const existingKey = String(process.env[spec.keyEnv] || "");
    if (existingKey.length < 8 || existingKey.length > 4096 || /[\r\n\0]/.test(existingKey)) {
      throw new Error("系统环境中没有可用的 API key");
    }
  } else {
    if (apiKey.length < 8 || apiKey.length > 4096 || /[\r\n\0]/.test(apiKey)) {
      throw new Error("API key 格式无效");
    }
    sessionProviderEnv[spec.keyEnv] = apiKey;
  }
  sessionProviderEnv[spec.confirmEnv] = "1";
  apiKey = "";
  try {
    await restartBackend();
    const response = await fetch("http://127.0.0.1:8766/api/provider-status");
    if (!response.ok) throw new Error("连接状态检查失败");
    return await response.json();
  } catch (error) {
    if (!useExistingEnvironmentKey) delete sessionProviderEnv[spec.keyEnv];
    delete sessionProviderEnv[spec.confirmEnv];
    await restartBackend().catch(() => {});
    throw error;
  }
});

async function waitForBackend() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch("http://127.0.0.1:8766/api/health");
      if (response.ok) return;
    } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("本地分析服务启动超时");
}

async function publishBackendHealth() {
  let healthy = false;
  try {
    const response = await fetch("http://127.0.0.1:8766/api/health");
    healthy = response.ok && (await response.json()).ok === true;
  } catch (_) {}
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("cureflow:backend-health", healthy);
  }
}

function startHealthMonitor() {
  publishBackendHealth();
  healthTimer = setInterval(publishBackendHealth, 30_000);
}

async function createWindow() {
  startBackend();
  await waitForBackend();
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

app.whenReady().then(createWindow).catch((error) => {
  dialog.showErrorBox("CureFlow 启动失败", error.message); app.quit();
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("before-quit", () => {
  if (healthTimer) clearInterval(healthTimer);
  if (backend && !backend.killed) backend.kill();
  for (const key of Object.keys(sessionProviderEnv)) delete sessionProviderEnv[key];
});
