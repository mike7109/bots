import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import http from "node:http";

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
        // keepAlive:false — иначе http-proxy переиспользует простаивающий сокет,
        // который uvicorn закрывает по своему keep-alive таймауту (~5 c). На
        // следующем запросе с телом (PATCH/POST) прокси утыкается в «мёртвый»
        // сокет и висит ~30 c — из-за этого перенос карточки на доске «не
        // срабатывал» в dev. Свежее соединение на запрос надёжнее. В проде фронт
        // отдаёт сам бэкенд (один origin), прокси нет — там этой проблемы не было.
        agent: new http.Agent({ keepAlive: false }),
      },
    },
  },
});
