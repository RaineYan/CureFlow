const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { readNativeDataFile } = require("../electron/data-file.cjs");

async function fixture(t, name, bytes) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "cureflow-file-test-"));
  t.after(() => fs.rm(folder, { recursive: true, force: true }));
  const file = path.join(folder, name);
  await fs.writeFile(file, bytes);
  return file;
}

test("native picker decodes GBK Chinese exactly like browser upload", async t => {
  const file = await fixture(t, "中文.csv", Buffer.from([0xd6, 0xd0, 0xce, 0xc4, 0x2c, 0x31, 0x0a]));
  assert.deepEqual(await readNativeDataFile(file), { filename: "中文.csv", csvText: "中文,1\n", xlsxBase64: "" });
});

test("native picker preserves UTF-8 Chinese and removes the BOM", async t => {
  const file = await fixture(t, "数据.CSV", Buffer.from("\ufeff名称,值\n甲,2\n"));
  assert.equal((await readNativeDataFile(file)).csvText, "名称,值\n甲,2\n");
});

test("native XLSX transport preserves every byte and clears CSV state", async t => {
  const bytes = Buffer.from([0x50, 0x4b, 0, 255, 128, 1]);
  const result = await readNativeDataFile(await fixture(t, "数据.xlsx", bytes));
  assert.equal(result.csvText, "");
  assert.deepEqual(Buffer.from(result.xlsxBase64, "base64"), bytes);
});

for (const [name, bytes, message] of [
  ["空.csv", Buffer.alloc(0), /空文件/],
  ["大.csv", Buffer.alloc(5 * 1024 * 1024 + 1), /5 MB/],
  ["旧.xls", Buffer.from("x"), /另存为 XLSX/],
]) {
  test(`native picker rejects ${name}`, async t => {
    await assert.rejects(readNativeDataFile(await fixture(t, name, bytes)), message);
  });
}

test("shared parser rejects a file that grows or empties after stat", async () => {
  const { readDataFile } = await import("../electron/data-format.mjs");
  await assert.rejects(readDataFile({ name: "x.csv", size: 1, arrayBuffer: async () => new ArrayBuffer(0) }), /空文件/);
  await assert.rejects(readDataFile({ name: "x.csv", size: 1, arrayBuffer: async () => new ArrayBuffer(5 * 1024 * 1024 + 1) }), /5 MB/);
});

test("packaged host file decoding works without the renderer source directory", async t => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "cureflow-packaged-file-test-"));
  t.after(() => fs.rm(folder, { recursive: true, force: true }));
  for (const name of ["data-file.cjs", "data-format.mjs"]) {
    await fs.copyFile(path.join(__dirname, "..", "electron", name), path.join(folder, name));
  }
  const { readNativeDataFile: packagedRead } = require(path.join(folder, "data-file.cjs"));
  const file = await fixture(t, "中文.csv", Buffer.from([0xd6, 0xd0, 0xce, 0xc4]));
  const result = await packagedRead(file);
  assert.equal(result.csvText, "中文");
  assert.deepEqual(Object.keys(result).sort(), ["csvText", "filename", "xlsxBase64"]);
  assert.ok(!JSON.stringify(result).includes(path.dirname(file)));
});
