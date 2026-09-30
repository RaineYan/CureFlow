const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const os = require("node:os");
const { waitForHealth } = require("./smoke-health.cjs");

const desktopRoot = path.resolve(__dirname, "..");
const distEntry = path.join(desktopRoot, "dist", "index.html");

async function reserveFreePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, "127.0.0.1", resolve).once("error", reject));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

function newestMtime(target) {
  if (!fs.existsSync(target)) return 0;
  const stat = fs.statSync(target);
  if (!stat.isDirectory()) return stat.mtimeMs;
  return fs.readdirSync(target, { withFileTypes: true }).reduce((latest, entry) => {
    const child = path.join(target, entry.name);
    return Math.max(latest, newestMtime(child));
  }, stat.mtimeMs);
}

function buildIfStale() {
  const sourceMtime = Math.max(
    newestMtime(path.join(desktopRoot, "src")),
    newestMtime(path.join(desktopRoot, "electron", "data-format.mjs")),
    newestMtime(path.join(desktopRoot, "public")),
    newestMtime(path.join(desktopRoot, "index.html")),
    newestMtime(path.join(desktopRoot, "vite.config.mjs")),
  );
  if (!process.argv.includes("--rebuild") && newestMtime(distEntry) >= sourceMtime) {
    console.log("[smoke] dist is current; skipping build");
    return;
  }
  console.log("[smoke] dist is stale; running npm run build");
  // Launch the JS entry directly: spawning npm.cmd without a shell fails on Windows.
  const result = spawnSync(process.execPath, [path.join(desktopRoot, "node_modules", "vite", "bin", "vite.js"), "build"], { cwd: desktopRoot, stdio: "inherit", windowsHide: true });
  if (result.error || result.status !== 0) throw new Error("前端构建失败");
}

async function stopElectron(child) {
  if (!child || child.exitCode !== null) return;
  const closed = new Promise(resolve => child.once("close", resolve));
  if (process.platform === "win32") {
    const result = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    if (result.error || result.status !== 0) throw new Error("无法结束冒烟测试进程，请检查残留进程");
  } else {
    child.kill("SIGTERM");
  }
  await Promise.race([closed, new Promise(resolve => setTimeout(resolve, 3_000))]);
}

async function main() {
  buildIfStale();
  const backendPort = await reserveFreePort();
  const electronPath = require("electron");
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "cureflow-smoke-"));
  const smokeEnv = { ...process.env, CUREFLOW_BACKEND_PORT: String(backendPort), CUREFLOW_SMOKE_USER_DATA: userData, PYTHONUTF8: "1" };
  delete smokeEnv.ELECTRON_RUN_AS_NODE;
  delete smokeEnv.CUREFLOW_DEV_URL;
  const child = spawn(electronPath, ["."], {
    cwd: desktopRoot,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: smokeEnv,
  });
  let launchError;
  child.on("error", error => { launchError = error; });
  child.stdout.on("data", (chunk) => process.stdout.write(chunk));
  child.stderr.on("data", (chunk) => process.stderr.write(chunk));
  try {
    await waitForHealth(`http://127.0.0.1:${backendPort}/api/health`, child);
    if (launchError) throw launchError;
    console.log("[smoke] PASS: CureFlow Electron and local backend are healthy");
  } finally {
    await stopElectron(child);
    try {
      fs.rmSync(userData, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    } catch (_) {
      console.warn(`[smoke] WARN: 临时缓存仍被占用，保留于 ${userData}`);
    }
  }
}

main().catch((error) => {
  console.error(`[smoke] FAIL: ${error.message}`);
  process.exitCode = 1;
});
