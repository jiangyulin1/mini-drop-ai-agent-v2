import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: ".",
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.js",
    include: ["src/**/*.{test,spec}.{js,jsx}"],
    css: false,
  },
  build: {
    outDir: "dist",
    // Ant Design and its rc-* runtime form one tightly coupled vendor graph.
    // Keep that cacheable graph intact; its minified size is ~1 MB (~320 KB gzip).
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
        manualChunks(id) {
          const modulePath = id.replaceAll("\\", "/");
          if (!modulePath.includes("/node_modules/")) return undefined;
          if (/\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(modulePath)) return "react";
          if (modulePath.includes("/node_modules/echarts-for-react/")) return "echarts-react";
          if (modulePath.includes("/node_modules/echarts/")) return "echarts";
          if (modulePath.includes("/node_modules/d3-flame-graph/")) return "flamegraph";
          if (/\/node_modules\/(d3|d3-[^/]+|internmap)\//.test(modulePath)) return "d3";
          if (/\/node_modules\/(antd|@ant-design\/[^/]+|@rc-component\/[^/]+|rc-[^/]+)\//.test(modulePath)) return "antd";
          if (modulePath.includes("/node_modules/axios/")) return "axios";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.MINI_DROP_WEB_API_TARGET || "http://localhost:8191",
        changeOrigin: true,
        // The three-node lab uses a pinned self-signed development certificate.
        // Production traffic is served by nginx and never uses this dev proxy.
        secure: false,
      },
    },
  },
});
