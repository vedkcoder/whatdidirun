import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built bundle ships inside the Python package and is served by FastAPI (`recall web`).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/recall/web/dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
