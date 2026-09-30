const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitForHealth(url, child, {
  timeoutMs = 30_000, intervalMs = 250, fetchImpl = fetch,
  now = Date.now, sleep = wait,
} = {}) {
  const deadline = now() + timeoutMs;
  while (now() < deadline) {
    if (child.exitCode !== null || child.signalCode) {
      throw new Error(`Electron 提前退出（code ${child.exitCode}）`);
    }
    try {
      const response = await fetchImpl(url, { signal: AbortSignal.timeout(Math.max(1, Math.min(2_000, deadline - now()))) });
      const payload = await response.json();
      if (response.ok && payload.ok === true && payload.service === "cureflow-local") return;
    } catch (_) {
      // Connection refusal while Python imports modules is expected during startup.
    }
    await sleep(intervalMs);
  }
  throw new Error("本地服务未在 30 秒内就绪；请检查 Python 依赖和启动日志");
}

module.exports = { waitForHealth };
