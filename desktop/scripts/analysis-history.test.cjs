"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { createAnalysisHistoryStore, MAX_ENTRIES } = require("../electron/analysis-history.cjs");

const encryption = {
  isEncryptionAvailable: () => true,
  encryptString: text => Buffer.from(`encrypted:${text}`, "utf8").reverse(),
  decryptString: buffer => Buffer.from(buffer).reverse().toString("utf8").slice("encrypted:".length),
};

const entry = {
  sourceKey: '["fingerprint","","file.csv"]',
  goal: "data_quality",
  config: { measureColumn: "", groupColumn: "", identifierColumn: "", timeColumn: "" },
  mode: "offline",
  modeIdentity: "offline",
  result: { runId: "run-1", input: { inputFingerprint: "fingerprint" }, report: "sensitive analysis" },
};

async function temporaryStore(t) {
  const userData = await fs.mkdtemp(path.join(os.tmpdir(), "cureflow-history-test-"));
  t.after(() => fs.rm(userData, { recursive: true, force: true }));
  return { userData, history: createAnalysisHistoryStore({ userData, encryption, platform: "win32" }) };
}

test("analysis history is opt-in, encrypted on disk, and recoverable", async t => {
  const { userData, history } = await temporaryStore(t);
  assert.deepEqual(await history.status(), { available: true, enabled: false, entries: [] });
  await assert.rejects(history.save([entry]), /先明确开启/);
  await history.enable();
  await history.save([entry]);
  const bytes = await fs.readFile(path.join(userData, "analysis-history.bin"));
  assert.equal(bytes.includes(Buffer.from("sensitive analysis")), false);
  assert.deepEqual((await history.status()).entries, [entry]);
  assert.deepEqual((await history.enable([entry])).entries, [entry]);
  await history.disable();
  assert.deepEqual(await history.status(), { available: true, enabled: false, entries: [] });
});

test("analysis history refuses invalid records and too many entries", async t => {
  const { history } = await temporaryStore(t);
  await history.enable();
  await assert.rejects(history.save([{ result: { runId: "x" } }]), /结构无效/);
  await assert.rejects(history.save(Array(MAX_ENTRIES + 1).fill(entry)), /数量无效/);
  assert.deepEqual((await history.status()).entries, []);
});

test("analysis history fails closed when encryption is unavailable or file is corrupt", async t => {
  const { userData, history } = await temporaryStore(t);
  const unavailable = createAnalysisHistoryStore({ userData, encryption, platform: "linux" });
  assert.deepEqual(await unavailable.status(), { available: false, enabled: false, entries: [] });
  await assert.rejects(unavailable.enable(), /暂不支持/);
  await history.enable();
  await fs.writeFile(path.join(userData, "analysis-history.bin"), "not encrypted");
  await assert.rejects(history.status(), /无法解密/);
  await assert.rejects(history.enable(), /无法解密/);
});
