import { defineConfig } from "vite";

// Дев-сервер проксирует /api, /message, /threads на FastAPI-бэкенд
// (app/api/app.py) — сам фронтенд не знает адрес бэкенда захардкоженным,
// в проде оба обычно отдаются с одного origin (см. app.mount("/static", ...)
// в app.py — можно собрать этот фронтенд в app/api/static/ и раздавать
// оттуда, тогда прокси не нужен вовсе).
export default defineConfig({
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/message": "http://localhost:8000",
      "/threads": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
