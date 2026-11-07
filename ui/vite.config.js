import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Compilé dans le paquet Python : le serveur sert ces fichiers sur « / ».
// En développement (`npm run dev`), l'API du serveur local est relayée.
export default defineConfig({
  plugins: [svelte()],
  build: { outDir: "../src/biblio/web", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
