import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

const backendOrigin = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue()],
  build: {
    target: "es2023",
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": { target: backendOrigin },
      "/proxy": { target: backendOrigin },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "tests/**/*.spec.ts"],
    restoreMocks: true,
  },
});
