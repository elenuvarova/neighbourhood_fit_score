import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Local dev: the FastAPI backend runs on :3001 (see README / Dockerfile).
      "/api": "http://localhost:3001",
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        // Split the heavy MapLibre GL dependency into its own long-cached
        // vendor chunk. App code changes then don't bust the ~330 KB (gzip)
        // map bundle, so repeat visits re-download only the small app chunk.
        manualChunks: {
          maplibre: ["maplibre-gl"],
        },
      },
    },
  },
});
