import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

// Unit tests only: the pure helpers in src/lib, the composables, and one jsdom smoke
// test that mounts `App.vue` (no browser, no styling). jsdom supplies the DOM types
// and storage the code is written against (ImageData, Uint8ClampedArray,
// localStorage); the Vue SFC plugin is the only one needed — the PWA and Tailwind
// plugins stay out of the run.
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
