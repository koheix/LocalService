import { useAuth } from "../context/useAuth";

/**
 * 仮のホーム画面。詳細な仕様(システム状態パネル・アプリ一覧・管理セクション)は
 * T-20 で docs/UI_HOME.md どおりに実装する。T-19時点ではログイン後の
 * 遷移先が存在することだけを確認する。
 */
export function Home() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <header className="flex items-center justify-between border-b border-gray-200 px-6 py-4 dark:border-gray-700">
        <span className="font-semibold text-gray-900 dark:text-gray-100">LLM Console</span>
        <div className="flex items-center gap-3 text-sm text-gray-600 dark:text-gray-300">
          <span>{user?.email}</span>
          <button
            type="button"
            onClick={() => void logout()}
            className="rounded-lg border border-gray-300 px-3 py-1 hover:bg-gray-100 dark:border-gray-600 dark:hover:bg-gray-800"
          >
            ログアウト
          </button>
        </div>
      </header>
      <main className="p-6 text-gray-500 dark:text-gray-400">
        ホーム画面は T-20 で実装します。
      </main>
    </div>
  );
}
