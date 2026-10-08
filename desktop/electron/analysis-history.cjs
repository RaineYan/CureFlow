"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const { randomBytes } = require("node:crypto");

const MAX_ENTRIES = 12;
const MAX_PLAINTEXT_BYTES = 8 * 1024 * 1024;

function createAnalysisHistoryStore({ userData, encryption, platform = process.platform }) {
  const filePath = path.join(userData, "analysis-history.bin");
  let mutation = Promise.resolve();
  function inOrder(action) {
    const next = mutation.then(action);
    mutation = next.catch(() => {});
    return next;
  }

  function available() {
    return platform === "win32" && encryption.isEncryptionAvailable();
  }

  function requireEncryption() {
    if (!available()) throw new Error("这台 Windows 电脑暂不支持加密保存分析记录");
  }

  function validateEntries(entries) {
    if (!Array.isArray(entries) || entries.length > MAX_ENTRIES) {
      throw new Error("分析记录数量无效");
    }
    for (const entry of entries) {
      if (!entry || typeof entry !== "object" || typeof entry.sourceKey !== "string"
          || typeof entry.goal !== "string" || typeof entry.mode !== "string"
          || typeof entry.modeIdentity !== "string" || !entry.config || typeof entry.config !== "object"
          || !entry.result || typeof entry.result.runId !== "string"
          || !entry.result.input || typeof entry.result.input.inputFingerprint !== "string") {
        throw new Error("分析记录结构无效");
      }
      const progress = entry.reviewProgress;
      if (progress !== undefined) {
        const validIds = value => Array.isArray(value) && value.length <= 10000
          && value.every(id => typeof id === "string" && /^fix_[0-9a-f]{16}$/.test(id));
        if (!progress || typeof progress !== "object"
            || progress.runId !== entry.result.runId
            || progress.inputFingerprint !== entry.result.input.inputFingerprint
            || !validIds(progress.selectedFixIds) || !validIds(progress.reviewedValidIds)
            || !progress.manualCorrections || typeof progress.manualCorrections !== "object"
            || Array.isArray(progress.manualCorrections)
            || Object.keys(progress.manualCorrections).length > 10000
            || Object.entries(progress.manualCorrections).some(([id, value]) =>
              !/^fix_[0-9a-f]{16}$/.test(id) || typeof value !== "string" || value.length > 5000)) {
          throw new Error("人工复核进度结构无效");
        }
      }
    }
    return entries;
  }

  async function read() {
    requireEncryption();
    let ciphertext;
    try {
      ciphertext = await fs.readFile(filePath);
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw new Error("无法读取本机分析记录");
    }
    try {
      const decoded = JSON.parse(encryption.decryptString(ciphertext));
      if (decoded.version !== 1) throw new Error("version");
      return validateEntries(decoded.entries);
    } catch {
      throw new Error("本机分析记录无法解密或已经损坏；可以在设置中删除后重新开启");
    }
  }

  async function write(entries) {
    requireEncryption();
    const serialized = Buffer.from(JSON.stringify({ version: 1, entries: validateEntries(entries) }), "utf8");
    if (serialized.length > MAX_PLAINTEXT_BYTES) throw new Error("分析记录过大，仍可在本次打开期间查看；不会写入磁盘");
    const ciphertext = encryption.encryptString(serialized.toString("utf8"));
    const temporary = `${filePath}.${randomBytes(6).toString("hex")}.tmp`;
    await fs.mkdir(userData, { recursive: true });
    try {
      await fs.writeFile(temporary, ciphertext, { flag: "wx" });
      await fs.rename(temporary, filePath);
    } finally {
      await fs.rm(temporary, { force: true }).catch(() => {});
    }
  }

  return {
    async status() {
      const canSave = available();
      if (!canSave) return { available: false, enabled: false, entries: [] };
      const entries = await read();
      return { available: true, enabled: entries !== null, entries: entries || [] };
    },
    enable(entries = []) { return inOrder(async () => {
      const existing = await read();
      if (existing !== null) return { available: true, enabled: true, entries: existing };
      await write(entries);
      return { available: true, enabled: true, entries };
    }); },
    save(entries) { return inOrder(async () => {
      if (await read() === null) throw new Error("请先明确开启本机加密保存");
      await write(entries);
      return { saved: entries.length };
    }); },
    disable() { return inOrder(async () => {
      await fs.rm(filePath, { force: true });
      return { available: available(), enabled: false, entries: [] };
    }); },
  };
}

module.exports = { createAnalysisHistoryStore, MAX_ENTRIES, MAX_PLAINTEXT_BYTES };
