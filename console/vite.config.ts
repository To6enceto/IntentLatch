import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The console calls the gateway's /console and /admin APIs on its own origin.
const gateway = process.env.INTENTLATCH_GATEWAY_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "^/(admin|console)/": { target: gateway },
    },
  },
});
