import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ['react-window'],
  },
  server: {
    host: '0.0.0.0', // 允许外部访问
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8180",
        changeOrigin: true
      },
      "/ws": {
        target: "ws://localhost:8180",
        ws: true,
        changeOrigin: true
      }
    }
  }
});

