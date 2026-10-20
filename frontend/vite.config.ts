import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The app talks to the FastAPI backend over `VITE_API_BASE_URL`. During
// development the browser loads the app from Vite's origin, so the backend
// must send CORS headers for it (the backend's BMS_CORS_ALLOW_ORIGINS already
// defaults to the Vite ports). Nothing here embeds credentials.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
