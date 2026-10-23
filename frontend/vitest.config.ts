import { defineConfig } from "vitest/config";

// 单测只覆盖纯函数（reducer / SSE 解析），不需要 Vite 插件链与 DOM 环境。
// 单独配置可避免 vitest 自带的 vite 5 类型与 vite 6 插件类型冲突。
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
