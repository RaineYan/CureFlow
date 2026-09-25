const MAX_FILE_BYTES = 5 * 1024 * 1024;

export function decodeCsvBuffer(buffer) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    try {
      return new TextDecoder("gb18030", { fatal: true }).decode(buffer);
    } catch {
      throw new Error("CSV 编码无法识别，请另存为 UTF-8 或 GBK 后重试。");
    }
  }
}

export async function readDataFile(file) {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (extension === "xls") throw new Error("这是旧版 XLS 文件，请在 Excel 中另存为 XLSX 后导入。");
  if (!["csv", "xlsx"].includes(extension)) throw new Error("请选择 CSV 或 XLSX 表格文件；图片、PDF 和文件夹暂不支持。");
  if (file.size === 0) throw new Error("所选文件是空文件，请确认保存完整后重新选择。");
  if (file.size > MAX_FILE_BYTES) throw new Error("数据文件不能超过 5 MB，请缩小工作表范围后重试。");
  let buffer;
  try { buffer = await file.arrayBuffer(); }
  catch { throw new Error("无法读取所选文件，请确认文件已下载到本机，并重新选择。"); }
  if (extension === "csv") return { filename: file.name, csvText: decodeCsvBuffer(buffer), xlsxBase64: "" };
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 32768) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
  }
  return { filename: file.name, csvText: "", xlsxBase64: btoa(binary) };
}
