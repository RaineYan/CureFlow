"use strict";

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { waitForHealth } = require("./smoke-health.cjs");

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, "127.0.0.1", resolve).once("error", reject));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function main() {
  if (process.platform !== "win32") throw new Error("打包验收仅支持 Windows");
  const executable = path.resolve(process.argv[2] || "");
  if (path.basename(executable).toLowerCase() !== "cureflow.exe" || !fs.existsSync(executable)) {
    throw new Error("请传入本次打包生成的 win-unpacked/CureFlow.exe");
  }
  const testRoot = fs.mkdtempSync(path.join(os.tmpdir(), "cureflow-packaged-smoke-"));
  const testAppData = path.join(testRoot, "AppData");
  fs.mkdirSync(testAppData);
  const port = await freePort();
  const env = {
    ...process.env, APPDATA: testAppData,
    CUREFLOW_SMOKE_USER_DATA: testRoot,
    CUREFLOW_BACKEND_PORT: String(port),
  };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.CUREFLOW_DEV_URL;
  const child = spawn(executable, [], {
    cwd: path.dirname(executable), windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env,
  });
  child.stdout.on("data", chunk => process.stdout.write(chunk));
  child.stderr.on("data", chunk => process.stderr.write(chunk));
  try {
    const base = `http://127.0.0.1:${port}`;
    await waitForHealth(`${base}/api/health`, child);
    const response = await fetch(`${base}/api/inspect`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: "verification.csv", csvText: "编号,分数\nA,80\nB,81\nC,82\nD,83\n" }),
    });
    const inspection = await response.json();
    if (!response.ok || inspection.rowCount !== 4 || inspection.providerUsed !== false) {
      throw new Error("打包后端未完成真实 CSV 读取检查");
    }
    for (const route of ["/api/sample", "/api/recovery-evidence"]) {
      if ((await fetch(`${base}${route}`)).status !== 404) throw new Error(`发行包仍暴露内部路径：${route}`);
    }
    console.log("[packaged-smoke] PASS: packaged window and backend started; CSV inspection works; internal fixtures are absent");
  } finally {
    if (child.exitCode === null) {
      const stopped = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
      if (stopped.status !== 0) console.warn("[packaged-smoke] WARN: 请检查测试窗口是否仍在运行");
      await Promise.race([
        new Promise(resolve => child.once("exit", resolve)),
        new Promise(resolve => setTimeout(resolve, 3000)),
      ]);
    }
    try { fs.rmSync(testRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 500 }); }
    catch { console.warn(`[packaged-smoke] WARN: 临时测试数据仍被占用：${testRoot}`); }
  }
}

main().catch(error => { console.error(`[packaged-smoke] FAIL: ${error.message}`); process.exitCode = 1; });
