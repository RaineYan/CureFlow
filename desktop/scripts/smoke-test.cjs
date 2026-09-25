const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");

const desktopRoot = path.resolve(__dirname, "..");
const distEntry = path.join(desktopRoot, "dist", "index.html");
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

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
    newestMtime(path.join(desktopRoot, "index.html")),
    newestMtime(path.join(desktopRoot, "vite.config.mjs")),
  );
  if (newestMtime(distEntry) >= sourceMtime) {
    console.log("[smoke] dist is current; skipping build");
    return;
  }
  console.log("[smoke] dist is stale; running npm run build");
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const result = spawnSync(npm, ["run", "build"], { cwd: desktopRoot, stdio: "inherit" });
  if (result.status !== 0) throw new Error("前端构建失败");
}

function stopElectron(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

async function main() {
  buildIfStale();
  const backendPort = await reserveFreePort();
  const electronPath = require("electron");
  const child = spawn(electronPath, ["."], {
    cwd: desktopRoot,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, CUREFLOW_BACKEND_PORT: String(backendPort), PYTHONUTF8: "1" },
  });
  child.stdout.on("data", (chunk) => process.stdout.write(chunk));
  child.stderr.on("data", (chunk) => process.stderr.write(chunk));
  try {
    await wait(5_000);
    if (child.exitCode !== null) throw new Error(`Electron 提前退出（code ${child.exitCode}）`);
    const response = await fetch(`http://127.0.0.1:${backendPort}/api/health`, {
      signal: AbortSignal.timeout(5_000),
    });
    const payload = await response.json();
    if (!response.ok || payload.ok !== true || payload.service !== "cureflow-local") {
      throw new Error(`健康检查返回异常（HTTP ${response.status}）`);
    }
    console.log("[smoke] PASS: CureFlow Electron and local backend are healthy");
  } finally {
    stopElectron(child);
  }
}

main().catch((error) => {
  console.error(`[smoke] FAIL: ${error.message}`);
  process.exitCode = 1;
});
