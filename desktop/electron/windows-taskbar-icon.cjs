const { spawn } = require("node:child_process");
const path = require("node:path");

// Electron's relaunch icon does not replace a live HWND's ICON_BIG.
// Set only that handle; ICON_SMALL (the titlebar) stays owned by Electron.
// Keep the helper alive for the window lifetime because it owns the HICON.
const script = String.raw`
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
public static class CureFlowWindowIcon {
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern IntPtr LoadImage(IntPtr instance, string name, uint type, int width, int height, uint flags);
  [DllImport("user32.dll", SetLastError=true)]
  static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint message, UIntPtr wparam, IntPtr lparam, uint flags, uint timeout, out UIntPtr result);
  [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
  [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
  static IntPtr Message(IntPtr hwnd, uint message, uint kind, IntPtr value) {
    UIntPtr result;
    if (SendMessageTimeout(hwnd, message, new UIntPtr(kind), value, 2, 2000, out result) == IntPtr.Zero)
      throw new Win32Exception(Marshal.GetLastWin32Error());
    return new IntPtr(unchecked((long)result.ToUInt64()));
  }
  public static void Run(long handle, uint owner, string file) {
    IntPtr window = new IntPtr(handle);
    uint actualOwner;
    GetWindowThreadProcessId(window, out actualOwner);
    if (!IsWindow(window) || actualOwner != owner) throw new InvalidOperationException("Window identity mismatch");
    IntPtr smallBefore = Message(window, 0x007F, 0, IntPtr.Zero);
    IntPtr icon = LoadImage(IntPtr.Zero, file, 1, GetSystemMetrics(11), GetSystemMetrics(12), 0x0010);
    if (icon == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
    IntPtr previous = IntPtr.Zero;
    bool assigned = false;
    try {
      previous = Message(window, 0x0080, 1, icon);
      assigned = true;
      if (Message(window, 0x007F, 1, IntPtr.Zero) != icon) throw new InvalidOperationException("Live taskbar icon did not change");
      if (Message(window, 0x007F, 0, IntPtr.Zero) != smallBefore) throw new InvalidOperationException("Titlebar icon unexpectedly changed");
      Console.WriteLine("CUREFLOW_ICON_READY: live big icon set; small icon unchanged");
      Console.Out.Flush();
      while (IsWindow(window)) {
        GetWindowThreadProcessId(window, out actualOwner);
        if (actualOwner != owner) break;
        Thread.Sleep(1000);
      }
    } finally {
      if (assigned && IsWindow(window)) {
        GetWindowThreadProcessId(window, out actualOwner);
        if (actualOwner == owner && Message(window, 0x007F, 1, IntPtr.Zero) == icon)
          Message(window, 0x0080, 1, previous);
      }
      DestroyIcon(icon);
    }
  }
}
'@
[CureFlowWindowIcon]::Run([long]$env:CUREFLOW_ICON_HWND, [uint32]$env:CUREFLOW_ICON_OWNER, $env:CUREFLOW_ICON_FILE)
`;

function setLiveTaskbarIcon(window, file) {
  if (process.platform !== "win32") return Promise.resolve({ applied: false });
  const handle = window.getNativeWindowHandle();
  const hwnd = handle.length === 8 ? handle.readBigUInt64LE().toString() : String(handle.readUInt32LE());
  const helper = spawn(path.join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
    ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")], {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        SystemRoot: process.env.SystemRoot || "C:\\Windows",
        TEMP: process.env.TEMP, TMP: process.env.TMP,
        CUREFLOW_ICON_HWND: hwnd, CUREFLOW_ICON_OWNER: String(process.pid), CUREFLOW_ICON_FILE: file,
      },
    });
  // Do not kill the icon owner on 'closed': it must release HICON after HWND dies.
  // Its window/owner check handles normal shutdown and app crashes alike.
  return new Promise((resolve, reject) => {
    let settled = false, output = "", error = "";
    const timeout = setTimeout(() => fail(new Error("任务栏图标设置超时")), 12000);
    function fail(reason) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      helper.kill();
      reject(reason);
    }
    helper.stdout.on("data", chunk => {
      output += chunk.toString();
      if (!settled && output.includes("CUREFLOW_ICON_READY")) {
        settled = true;
        clearTimeout(timeout);
        helper.unref();
        resolve({ applied: true, titlebarUnchanged: true });
      }
    });
    helper.stderr.on("data", chunk => { error = (error + chunk.toString()).slice(-2000); });
    helper.on("error", fail);
    helper.on("exit", code => { if (!settled) fail(new Error(`任务栏图标设置失败 (${code}): ${error}`)); });
  });
}
module.exports = { setLiveTaskbarIcon };
