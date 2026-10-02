import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000 (`cs2-analyzer serve`); Vite forwards API calls to it.
const api = "http://localhost:8000";
const paths = ["/auth", "/me", "/matches", "/players", "/risk", "/jobs", "/health", "/site-info", "/companion", "/lobby"];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(paths.map((p) => [p, api])),
    // The How it works and Credits pages import docs/how-it-works.md and CREDITS.md from the repository.
    fs: { allow: [".", "../docs", "../CREDITS.md"] },
  },
});
