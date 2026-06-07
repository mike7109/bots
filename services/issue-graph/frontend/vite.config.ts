import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Бэкенд по умолчанию на :8000. Фронт ходит туда напрямую (CORS разрешён),
// либо через прокси /api ниже, если открыт без VITE_API.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
