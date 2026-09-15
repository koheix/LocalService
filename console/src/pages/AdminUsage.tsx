import { useEffect, useState } from "react";
import { Header } from "../components/Header";
import {
  fetchUsage,
  listModels,
  listUsers,
  type ModelOut,
  type UsageGroupBy,
  type UsageRow,
  type UserOut,
} from "../lib/adminApi";

const GROUP_BY_LABELS: Record<UsageGroupBy, string> = {
  day: "日別",
  user: "ユーザー別",
  model: "モデル別",
};

function toDateInputValue(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - 7);
  return { from: toDateInputValue(from), to: toDateInputValue(to) };
}

/**
 * 管理画面: 利用状況(T-28)。GET /api/admin/usage をそのまま表示する。
 * user/model別の集計はID(数値)しか返らないため、GET /api/admin/users・
 * /api/admin/models から表示名を解決する(この画面専用。ユーザー管理・
 * モデル管理画面自体は別タスク)。
 */
export function AdminUsage() {
  const [groupBy, setGroupBy] = useState<UsageGroupBy>("day");
  const [range, setRange] = useState(defaultRange);
  const [rows, setRows] = useState<UsageRow[] | null>(null);
  const [users, setUsers] = useState<UserOut[]>([]);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listUsers()
      .then(setUsers)
      .catch(() => {
        // 名前解決に失敗してもID表示にフォールバックするので致命的ではない。
      });
    listModels()
      .then(setModels)
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    // toは日付入力(00:00始まり)なので、その日を含めるため翌日00:00未満まで
    // (gateway側は from<=created_at<to の半開区間、docs/API.md参照)。
    const toExclusive = new Date(`${range.to}T00:00:00Z`);
    toExclusive.setUTCDate(toExclusive.getUTCDate() + 1);
    fetchUsage({
      from: `${range.from}T00:00:00Z`,
      to: toExclusive.toISOString(),
      group_by: groupBy,
    })
      .then((res) => {
        if (cancelled) return;
        setRows(res.data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setRows([]);
        setError(err instanceof Error ? err.message : "利用状況の取得に失敗しました");
      });
    return () => {
      cancelled = true;
    };
  }, [groupBy, range]);

  function keyLabel(row: UsageRow): string {
    if (groupBy === "day") return row.day ?? "(不明)";
    if (groupBy === "user") {
      if (row.user == null) return "(不明)";
      const matched = users.find((candidate) => candidate.id === row.user);
      return matched ? matched.email : `#${row.user}`;
    }
    if (row.model == null) return "(不明)";
    const matched = models.find((candidate) => candidate.id === row.model);
    return matched ? matched.served_name : `#${row.model}`;
  }

  return (
    <div className="min-h-screen bg-white dark:bg-gray-900">
      <Header />
      <main className="mx-auto max-w-[960px] p-4 sm:p-6">
        <h1 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">利用状況</h1>

        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
            開始日
            <input
              type="date"
              value={range.from}
              max={range.to}
              onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
              className="rounded-lg border border-gray-300 bg-white px-2 py-1 text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-gray-600 dark:text-gray-300">
            終了日
            <input
              type="date"
              value={range.to}
              min={range.from}
              onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
              className="rounded-lg border border-gray-300 bg-white px-2 py-1 text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            />
          </label>
          <div className="flex gap-1" role="group" aria-label="集計単位">
            {(Object.keys(GROUP_BY_LABELS) as UsageGroupBy[]).map((g) => (
              <button
                key={g}
                type="button"
                aria-pressed={groupBy === g}
                onClick={() => setGroupBy(g)}
                className={
                  "rounded-lg px-3 py-1.5 text-sm font-medium " +
                  (groupBy === g
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700")
                }
              >
                {GROUP_BY_LABELS[g]}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p className="mb-3 text-sm text-red-600 dark:text-red-400" role="alert">
            {error}
          </p>
        )}

        <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500 dark:bg-gray-800/60 dark:text-gray-400">
              <tr>
                <th className="px-4 py-2 font-medium">{GROUP_BY_LABELS[groupBy].replace("別", "")}</th>
                <th className="px-4 py-2 font-medium">リクエスト数</th>
                <th className="px-4 py-2 font-medium">入力トークン</th>
                <th className="px-4 py-2 font-medium">出力トークン</th>
                <th className="px-4 py-2 font-medium">合計トークン</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {rows === null ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-gray-400">
                    読み込み中…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-gray-400">
                    データがありません
                  </td>
                </tr>
              ) : (
                rows.map((row, i) => (
                  <tr key={i}>
                    <td className="px-4 py-2 text-gray-900 dark:text-gray-100">{keyLabel(row)}</td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                      {row.request_count.toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                      {row.prompt_tokens.toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                      {row.completion_tokens.toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                      {(row.prompt_tokens + row.completion_tokens).toLocaleString()}
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
