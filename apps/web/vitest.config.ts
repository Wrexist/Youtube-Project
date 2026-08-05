import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

/** The web app had no test runner at all — CLAUDE.md named `vitest` as the
 *  convention and it was never installed, so ten screens were checked by nothing
 *  but `tsc` and `eslint`. */
export default defineConfig({
  // Cast: the app pins Vite 7 while @vitejs/plugin-react resolves Vite 6 types.
  // The plugin itself is compatible; only the two Plugin declarations differ.
  // The app pins Vite 7; @vitejs/plugin-react still resolves Vite 6's type
  // declarations. The plugin is compatible at runtime — only the two `Plugin`
  // interfaces differ — so this is a version-skew cast, not a suppression of a
  // real error.
  plugins: [react() as unknown as never],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "."),
      "@studio/contracts": resolve(__dirname, "../../packages/contracts/src"),
    },
  },
});
