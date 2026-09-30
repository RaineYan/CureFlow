"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");

async function readNativeDataFile(filePath) {
  // Reuse the browser decoder so choosing a file and dropping it behave alike.
  const { readDataFile } = await import("./data-format.mjs");
  const stat = await fs.stat(filePath);
  return readDataFile({
    name: path.basename(filePath),
    size: stat.size,
    async arrayBuffer() {
      const bytes = await fs.readFile(filePath);
      // The file can change after stat (for example while another app saves it).
      return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    },
  });
}

module.exports = { readNativeDataFile };
