import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative asset URLs plus the <base href> the server injects let the app live at / or under a prefix.
const api = process.env.LUMACAL_API ?? "http://127.0.0.1:8771";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { target: "es2022", sourcemap: false, assetsInlineLimit: 0 },
  server: {
    host: "127.0.0.1",
    proxy: { "/api": api, "/feed.ics": api },
  },
});
