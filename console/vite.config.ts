import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev` 単体で動かす場合、/api は起動済みのCaddy(proxy:8080)経由で
    // gatewayに転送する。docker compose 経由(ビルド後にCaddyから配信)の場合は
    // このプロキシ設定自体は使われない。
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
})
