import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAdmin } from "./components/RequireAdmin";
import { RequireAuth } from "./components/RequireAuth";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Placeholder } from "./pages/Placeholder";
import { Playground } from "./pages/Playground";

function placeholderRoute(path: string, title: string, note: string, adminOnly = false) {
  const content = adminOnly ? (
    <RequireAdmin>
      <Placeholder title={title} note={note} />
    </RequireAdmin>
  ) : (
    <Placeholder title={title} note={note} />
  );
  return <Route key={path} path={path} element={<RequireAuth>{content}</RequireAuth>} />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Home />
          </RequireAuth>
        }
      />
      <Route
        path="/playground"
        element={
          <RequireAuth>
            <Playground />
          </RequireAuth>
        }
      />
      {placeholderRoute(
        "/rag",
        "社内文書検索",
        "この画面はT-22で実装します。資料をアップロードして質問できます。",
      )}
      {placeholderRoute(
        "/transcription",
        "文字起こし",
        "録音から議事録を作る機能です。Phase 2以降で実装します。",
      )}
      {placeholderRoute(
        "/api-keys",
        "API キー",
        "外部ツールから接続するためのAPIキーを発行・管理する画面です。今後実装します。",
      )}
      {placeholderRoute(
        "/admin/users",
        "ユーザー管理",
        "ユーザーの一覧・作成・権限変更を行う画面です。今後実装します。",
        true,
      )}
      {placeholderRoute(
        "/admin/models",
        "モデル管理",
        "利用可能なモデルの一覧・有効化設定を行う画面です。今後実装します。",
        true,
      )}
      {placeholderRoute(
        "/admin/usage",
        "利用状況",
        "ユーザー・モデル別の利用状況を確認する画面です。今後実装します。",
        true,
      )}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
