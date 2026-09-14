import { Boxes, ChartColumn, Users, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchSummary, type SummaryResponse } from "../lib/adminApi";

type Row = {
  name: string;
  icon: LucideIcon;
  to: string;
  value: (s: SummaryResponse) => string;
};

const ROWS: Row[] = [
  {
    name: "ユーザー管理",
    icon: Users,
    to: "/admin/users",
    value: (s) => `${s.active_user_count.toLocaleString()} 人`,
  },
  {
    name: "モデル管理",
    icon: Boxes,
    to: "/admin/models",
    value: (s) => `${s.active_model_count.toLocaleString()} 件`,
  },
  {
    name: "利用状況",
    icon: ChartColumn,
    to: "/admin/usage",
    value: (s) => `今日 ${s.today_total_tokens.toLocaleString()} tok`,
  },
];

/** adminロールのときのみ呼び出し元(Home)がレンダーする前提。 */
export function AdminSection() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSummary()
      .then((s) => {
        if (!cancelled) setSummary(s);
      })
      .catch(() => {
        // 件数取得に失敗しても管理セクション自体は表示する("—"のまま)。
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-300">管理</h2>
      <div className="divide-y divide-gray-200 rounded-xl border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
        {ROWS.map((row) => {
          const Icon = row.icon;
          return (
            <Link
              key={row.name}
              to={row.to}
              className="flex items-center justify-between px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/60"
            >
              <span className="flex items-center gap-3">
                <Icon className="h-5 w-5 text-gray-500 dark:text-gray-400" aria-hidden />
                <span className="text-gray-900 dark:text-gray-100">{row.name}</span>
              </span>
              <span className="text-sm text-gray-500 dark:text-gray-400">
                {summary ? row.value(summary) : "—"}
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
