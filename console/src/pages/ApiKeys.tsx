import { Copy, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Header } from "../components/Header";
import {
  createApiKey,
  listApiKeys,
  revokeApiKey,
  type ApiKeyCreated,
  type ApiKeyOut,
} from "../lib/apiKeysApi";

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ja-JP");
}

/**
 * APIキー画面(T-30)。バックエンド(POST/GET/DELETE /api/auth/api-keys、T-05)
 * は無変更で、自分のAPIキーを発行・一覧・失効できる。admin限定ではない
 * (ユーザーが自分自身のキーを管理する画面)。
 */
export function ApiKeys() {
  const [keys, setKeys] = useState<ApiKeyOut[] | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<number | null>(null);

  const refresh = async () => {
    const list = await listApiKeys();
    setKeys(list);
  };

  useEffect(() => {
    let cancelled = false;
    listApiKeys()
      .then((list) => {
        if (cancelled) return;
        setKeys(list);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "APIキー一覧の取得に失敗しました");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError(null);
    try {
      const result = await createApiKey(trimmed);
      setCreated(result);
      setCopied(false);
      setCopyFailed(false);
      setName("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "APIキーの発行に失敗しました");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: number) {
    if (!window.confirm("このAPIキーを失効させます。よろしいですか？")) return;
    setRevokingId(id);
    setError(null);
    try {
      await revokeApiKey(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "APIキーの失効に失敗しました");
    } finally {
      setRevokingId(null);
    }
  }

  async function handleCopy() {
    if (!created) return;
    // navigator.clipboardはセキュアコンテキスト(HTTPSまたはlocalhost)でしか
    // 使えない。この画面は社内LAN上でHTTPのまま(IPアドレス/ホスト名で)
    // アクセスされる可能性があるため、その場合はundefinedになる。
    // 失敗時は黙らず、下に表示中のキーを手動で選択・コピーするよう促す。
    if (!navigator.clipboard) {
      setCopyFailed(true);
      return;
    }
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopyFailed(true);
    }
  }

  return (
    <div className="min-h-screen bg-white dark:bg-gray-900">
      <Header />
      <main className="mx-auto max-w-[720px] p-4 sm:p-6">
        <h1 className="mb-1 text-lg font-semibold text-gray-900 dark:text-gray-100">API キー</h1>
        <p className="mb-4 text-sm text-gray-500 dark:text-gray-400">
          外部ツール(Dify、LangChain、OpenAI SDK等)から
          <code className="mx-1 rounded bg-gray-100 px-1 py-0.5 text-xs dark:bg-gray-800">
            Authorization: Bearer sk-...
          </code>
          で接続するためのAPIキーを発行・管理します。
        </p>

        {error && (
          <p className="mb-3 text-sm text-red-600 dark:text-red-400" role="alert">
            {error}
          </p>
        )}

        {created && (
          <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-900/20">
            <p className="mb-2 text-sm font-medium text-amber-800 dark:text-amber-200">
              「{created.name}」を発行しました。このキーは二度と表示されません。今すぐ控えてください。
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 overflow-x-auto rounded-lg bg-white px-3 py-2 text-sm text-gray-900 dark:bg-gray-900 dark:text-gray-100">
                {created.key}
              </code>
              <button
                type="button"
                onClick={handleCopy}
                className="flex shrink-0 items-center gap-1 rounded-lg bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700"
              >
                <Copy className="h-4 w-4" aria-hidden />
                {copied ? "コピーしました" : "コピー"}
              </button>
            </div>
            {copyFailed && (
              <p className="mt-2 text-sm text-amber-800 dark:text-amber-200" role="alert">
                自動コピーに失敗しました。上のキーを選択して手動でコピーしてください。
              </p>
            )}
          </div>
        )}

        <form onSubmit={handleCreate} className="mb-6 flex flex-wrap items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
            名前
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: Dify連携用"
              className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            />
          </label>
          <button
            type="submit"
            disabled={creating}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            発行する
          </button>
        </form>

        <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500 dark:bg-gray-800/60 dark:text-gray-400">
              <tr>
                <th className="px-4 py-2 font-medium">名前</th>
                <th className="px-4 py-2 font-medium">キー</th>
                <th className="px-4 py-2 font-medium">作成日</th>
                <th className="px-4 py-2 font-medium">最終利用日</th>
                <th className="px-4 py-2 font-medium">
                  <span className="sr-only">操作</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {keys === null ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-gray-400">
                    読み込み中…
                  </td>
                </tr>
              ) : keys.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-gray-400">
                    APIキーがありません
                  </td>
                </tr>
              ) : (
                keys.map((k) => (
                  <tr key={k.id}>
                    <td className="px-4 py-2 text-gray-900 dark:text-gray-100">{k.name}</td>
                    <td className="px-4 py-2 font-mono text-gray-500 dark:text-gray-400">
                      {k.prefix}…
                    </td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                      {formatDateTime(k.created_at)}
                    </td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                      {k.last_used_at ? formatDateTime(k.last_used_at) : "未使用"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => handleRevoke(k.id)}
                        disabled={revokingId === k.id}
                        aria-label={`${k.name}を失効させる`}
                        className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-red-600 hover:bg-red-50 disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-900/30"
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                        失効
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
