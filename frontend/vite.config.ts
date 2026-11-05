import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    VitePWA({
      // New deploys activate on the next launch without a user prompt.
      registerType: "autoUpdate",
      includeAssets: [
        "favicon.ico",
        "favicon.svg",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "apple-touch-icon-152x152.png",
        "apple-touch-icon-167x167.png",
        "apple-touch-icon-180x180.png",
        "logo.svg",
      ],
      manifest: {
        name: "Pokeum — Pokémon card scanner",
        short_name: "Pokeum",
        description: "Scan and identify Pokémon cards, then export your collection.",
        // The brand set's own manifest values (docs/brand/guidelines.md §2): a
        // manifest holds one colour, so it carries the light pairing; the dark
        // counterpart (#0A0A0A) lives in index.html's media-scoped theme-color metas.
        theme_color: "#FFFFFF",
        background_color: "#FFFFFF",
        display: "standalone",
        orientation: "portrait",
        icons: [
          { src: "pwa-64x64.png", sizes: "64x64", type: "image/png" },
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          {
            src: "maskable-icon-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        navigateFallback: "/index.html",
        // API responses must never be answered by the SW's navigation fallback.
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // Card art straight from the TCGdex CDN: content-addressed by card,
            // so CacheFirst is safe and makes the collection render offline.
            urlPattern: ({ url }) => url.hostname === "assets.tcgdex.net",
            handler: "CacheFirst",
            options: {
              cacheName: "card-art",
              cacheableResponse: { statuses: [0, 200] },
              expiration: { maxEntries: 1500, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
          {
            // Fallback path when a card has no CDN URL: the API thumbnail.
            urlPattern: /\/api\/cards\/[^/]+\/image$/,
            handler: "CacheFirst",
            options: {
              cacheName: "card-art-api",
              cacheableResponse: { statuses: [0, 200] },
              expiration: { maxEntries: 500, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Accessible through tunnels (cloudflared, ngrok) — Vite otherwise rejects
    // unknown hostnames with a misleading blocked-request error.
    allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ".ngrok.io"],
    // Proxy API calls to the pokeum FastAPI service so no CORS is needed.
    // The service mounts its routes under /api, so paths forward unchanged.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ".ngrok.io"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
