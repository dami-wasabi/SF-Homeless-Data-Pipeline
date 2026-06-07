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
  define: {
    // Expose the API URL to the built app
    // Works with both VITE_ prefix (recommended) and REACT_APP_ (legacy CRA)
    __API_URL__: JSON.stringify(process.env.VITE_API_URL || process.env.REACT_APP_API_URL || ""),
  },
});