import { useState, type FormEvent } from "react";
import { Navigate, useLocation, type Location } from "react-router-dom";
import { useAuth } from "../context/useAuth";
import { ApiError } from "../lib/api";

export function Login() {
  const { user, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) {
    // ログイン前に別ページへのアクセスで/loginへ飛ばされた場合(RequireAuth)は
    // 元のページへ戻す。ログイン成功後もuserが更新されてここを通るため、
    // handleSubmit側でnavigateを呼ぶ必要はない。pathnameだけでなくsearch/hash
    // も含めて復帰させる(クエリ付きの深リンクを壊さない)。
    const from = (location.state as { from?: Location } | null)?.from ?? "/";
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("メールアドレスまたはパスワードが違います");
      } else {
        setError(err instanceof Error ? err.message : "ログインに失敗しました");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 dark:bg-gray-900">
      <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <h1 className="mb-1 text-xl font-semibold text-gray-900 dark:text-gray-100">
          LLM Console
        </h1>
        <p className="mb-6 text-sm text-gray-500 dark:text-gray-400">
          社内アカウントでログインしてください
        </p>
        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          <label className="flex flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
            メールアドレス
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
            パスワード
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            />
          </label>
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
          >
            ログイン
          </button>
        </form>
      </div>
    </div>
  );
}
