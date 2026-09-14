# console

llm-console の Web UI。React 18 + TypeScript + Vite + Tailwind CSS の SPA。
方針は `docs/DECISIONS.md` の D-017、画面仕様は `docs/UI_HOME.md` を参照。

## 開発

通常は `docker compose up` で `console` コンテナごとビルド・起動する
（プロジェクトルートの `README.md` / `Makefile` 参照）。

このディレクトリ単体で `npm run dev` を動かす場合は、`vite.config.ts` の
`server.proxy` が `/api` を `http://localhost:8080` （`proxy` コンテナ）へ
転送する設定になっているため、事前に `docker compose up proxy gateway
postgres` 等でバックエンド一式を起動しておくこと。

```bash
npm install
npm run dev
```

## ビルド・Lint

```bash
npm run build   # tsc -b && vite build (dist/ に出力)
npm run lint    # oxlint
```

## 構成

```
src/
  lib/api.ts            gatewayへのfetchラッパー(401検知でセッション切れを通知)
  context/AuthContext.tsx  認証状態(GET /api/auth/me で復元)
  context/useAuth.ts       useAuthフック
  components/RequireAuth.tsx  未認証なら/loginへリダイレクトするルートガード
  pages/Login.tsx        ログイン画面
  pages/Home.tsx         ホーム画面(詳細はT-20でdocs/UI_HOME.mdどおりに実装)
```

## 配信

`Dockerfile` は `node:20-alpine` でビルドし、`caddy:2-alpine` に成果物
（`dist/`）をコピーして配信するマルチステージビルド。`Caddyfile` は
SPA 向けに `try_files {path} /index.html` で全パスを `index.html` に
フォールバックする。
