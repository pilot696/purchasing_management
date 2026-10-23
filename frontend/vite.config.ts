import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// dev 代理：前端 5173 -> 后端 8000（SSE 也走代理，保持相对路径）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/readyz": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
