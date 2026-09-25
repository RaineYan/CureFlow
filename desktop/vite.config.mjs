import { timingSafeEqual } from "node:crypto";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function lanAccessGuard() {
  return {
    name: "cureflow-lan-access-guard",
    configureServer(server) {
      const expected = process.env.CUREFLOW_LAN_ACCESS_TOKEN;
      server.middlewares.use((request, response, next) => {
        if (!request.url?.startsWith("/api/")) return next();
        const localPeer = ["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(request.socket.remoteAddress);
        if ((request.url.split("?", 1)[0] === "/api/provider/configure" ||
             request.url.split("?", 1)[0] === "/api/provider/probe" ||
             request.url.split("?", 1)[0].startsWith("/api/keys/")) &&
            (expected || !localPeer)) {
          response.writeHead(403, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
          response.end(JSON.stringify({ error: "模型配置仅允许本机页面操作" }));
          return;
        }
        // A same-origin GET normally has no Origin header. Forward one only
        // for the local read-only status endpoint; never for LAN visitors.
        if (localPeer && request.method === "GET" && request.url.split("?", 1)[0] === "/api/keys/status" && !request.headers.origin) {
          request.headers.origin = `http://${request.headers.host}`;
        }
        if (!expected) return next();
        const supplied = request.headers["x-cureflow-lan-token"];
        const actual = Buffer.from(typeof supplied === "string" ? supplied : "");
        const secret = Buffer.from(expected);
        if (actual.length === secret.length && timingSafeEqual(actual, secret)) return next();
        response.writeHead(401, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
        response.end(JSON.stringify({ error: "请先输入本次局域网访问码" }));
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), lanAccessGuard()],
  base: "./",
  server: {
    port: 5173,
    strictPort: true,
    // Browser clients use /api on this origin. Only Vite is exposed to the
    // LAN; the Python API remains private on the host computer.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8766",
        changeOrigin: false,
      },
    },
  },
});
