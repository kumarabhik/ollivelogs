import path from "path";
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:3000",
    headless: true,
  },
  webServer: {
    command: "npm -w apps/web run dev -- --hostname 127.0.0.1 --port 3000",
    cwd: path.resolve(__dirname, "../.."),
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
  },
});
