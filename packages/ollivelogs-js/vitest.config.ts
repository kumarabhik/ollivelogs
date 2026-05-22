import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    coverage: {
      exclude: [
        "coverage/**",
        "dist/**",
        "src/types.ts",
        "test/**",
        "tsup.config.ts",
        "vitest.config.ts",
      ],
    },
  },
});
