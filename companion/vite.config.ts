import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The windows' pages. `npm run dev:ui` serves them in a normal browser with a simulated app
// (src/renderer/bridge.ts), so the UI can be worked on without Electron, Overwolf or CS2.
export default defineConfig({
  root: resolve(__dirname, "src/renderer"),
  base: "./",
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "dist/renderer"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: resolve(__dirname, "src/renderer/index.html"),
        overlay: resolve(__dirname, "src/renderer/overlay.html"),
      },
    },
  },
  test: {
    root: __dirname,
    include: ["test/**/*.test.ts"],
  },
} as any);
