"use strict";

const { randomBytes } = require("node:crypto");
const { spawn } = require("node:child_process");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");
const projectDir = path.resolve(desktopDir, "..");
const accessCode = randomBytes(16).toString("hex");
const webEnv = {
  ...process.env,
  CUREFLOW_LAN_ACCESS_TOKEN: accessCode,
  VITE_LAN_MODE: "1",
};
const backendEnv = { ...process.env };
const processes = [];
let stopping = false;

function start(command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env, stdio: "inherit" });
  processes.push(child);
  child.on("error", (error) => {
    console.error(`启动失败：${error.message}`);
    stop(1);
  });
  child.on("exit", (code) => {
    if (!stopping) stop(code || 1);
  });
  return child;
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of processes) {
    if (child.exitCode === null) child.kill();
  }
  process.exitCode = code;
}

process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());

console.log("CureFlow 局域网模式：只适用于可信任的同一 Wi-Fi，不要暴露到公网。");
console.log(`本次访问码：${accessCode}`);
console.log("手机打开下方 Network 链接后输入访问码。停止服务后访问码失效。\n");

start(
  process.env.CUREFLOW_PYTHON || "python",
  [path.join(projectDir, "cureflow_app", "server.py"), "--port", "8766", "--data-dir", path.join(projectDir, ".cureflow-desktop-data")],
  projectDir,
  backendEnv,
);
start(process.execPath, [path.join(desktopDir, "node_modules", "vite", "bin", "vite.js"), "--host", "0.0.0.0"], desktopDir, webEnv);
