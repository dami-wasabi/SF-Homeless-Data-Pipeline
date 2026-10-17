import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "build",
  },
  server: {
    port: 3000,
    proxy: {
      "/summary":    "http://localhost:3001",
      "/encounters": "http://localhost:3001",
      "/shelters":   "http://localhost:3001",
    },
  },
});