import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the page runs on Vite and calls the API of `prism dashboard` on 8090.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8090" } },
  build: { outDir: "dist", emptyOutDir: true },
});
