import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { UsageGroupBy, UsageRow } from "../../lib/adminApi";

/** OSのダークモード設定を購読する。tailwindのdarkMode:"media"と同じ基準
 * (この画面ではchartがSVGの生スタイルを直接持つため、Tailwindのdark:
 * バリアントが使えずJS側で色を出し分ける必要がある)。 */
function usePrefersDark(): boolean {
  const [prefersDark, setPrefersDark] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches,
  );
  useEffect(() => {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => setPrefersDark(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return prefersDark;
}

function truncateLabel(label: string, max = 24): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

const formatNumber = (v: number) => v.toLocaleString();

// Rechartsの<Tooltip formatter>はvalueがstring|number|配列と広く型付けられて
// いるため、専用の緩い型で受けてから数値だけtoLocaleStringする。
function formatTooltipValue(value: unknown): string {
  if (typeof value === "number") return value.toLocaleString();
  if (Array.isArray(value)) return value.map((v) => formatTooltipValue(v)).join(", ");
  return String(value);
}

type ChartRow = {
  key: string;
  prompt_tokens: number;
  completion_tokens: number;
  request_count: number;
};

export function UsageChart({
  rows,
  groupBy,
  keyLabel,
}: {
  rows: UsageRow[];
  groupBy: UsageGroupBy;
  keyLabel: (row: UsageRow) => string;
}) {
  const prefersDark = usePrefersDark();
  const textColor = prefersDark ? "#9ca3af" : "#6b7280"; // gray-400 / gray-500
  const gridColor = prefersDark ? "#374151" : "#e5e7eb"; // gray-700 / gray-200
  const tooltipStyle = {
    backgroundColor: prefersDark ? "#1f2937" : "#ffffff", // gray-800 / white
    border: `1px solid ${gridColor}`,
    borderRadius: 8,
    color: prefersDark ? "#f3f4f6" : "#111827",
    fontSize: 12,
  };
  const promptColor = "#3b82f6"; // blue-500
  const completionColor = prefersDark ? "#60a5fa" : "#93c5fd"; // blue-400 / blue-300
  const requestColor = "#f59e0b"; // amber-500

  if (rows.length === 0) return null;

  // keyには全文を入れる(軸の表示だけtickFormatterで切り詰める)。ここで
  // 切り詰めてしまうと、Tooltipのlabelも切り詰め後の文字列になり全文が
  // 二度と見えなくなるほか、別のユーザー/モデルが同じ切り詰め結果に
  // 衝突して見分けが付かなくなる(集計自体は別バーのまま壊れないが、
  // ラベルだけが紛らわしくなる)。
  const data: ChartRow[] = rows.map((row) => ({
    key: groupBy === "day" ? (row.day ?? "(不明)") : keyLabel(row),
    prompt_tokens: row.prompt_tokens,
    completion_tokens: row.completion_tokens,
    request_count: row.request_count,
  }));

  if (groupBy === "day") {
    return (
      <div className="mb-4 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
            <XAxis dataKey="key" tick={{ fill: textColor, fontSize: 12 }} />
            <YAxis
              yAxisId="tokens"
              tick={{ fill: textColor, fontSize: 12 }}
              tickFormatter={formatNumber}
              label={{ value: "トークン数", angle: -90, position: "insideLeft", fill: textColor, fontSize: 12 }}
            />
            <YAxis
              yAxisId="requests"
              orientation="right"
              tick={{ fill: textColor, fontSize: 12 }}
              tickFormatter={formatNumber}
              label={{
                value: "リクエスト数",
                angle: 90,
                position: "insideRight",
                fill: textColor,
                fontSize: 12,
              }}
            />
            <Tooltip contentStyle={tooltipStyle} formatter={formatTooltipValue} />
            <Legend wrapperStyle={{ fontSize: 12, color: textColor }} />
            <Bar
              yAxisId="tokens"
              dataKey="prompt_tokens"
              stackId="tokens"
              name="入力トークン"
              fill={promptColor}
            />
            <Bar
              yAxisId="tokens"
              dataKey="completion_tokens"
              stackId="tokens"
              name="出力トークン"
              fill={completionColor}
            />
            <Line
              yAxisId="requests"
              dataKey="request_count"
              name="リクエスト数"
              stroke={requestColor}
              strokeWidth={2}
              dot={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ユーザー別/モデル別: 名前ごとの合計トークン数を横棒グラフで比較する。
  const height = Math.max(160, data.length * 36 + 40);
  return (
    <div className="mb-4 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 8, left: 8, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} horizontal={false} />
          <XAxis type="number" tick={{ fill: textColor, fontSize: 12 }} tickFormatter={formatNumber} />
          <YAxis
            type="category"
            dataKey="key"
            width={140}
            tick={{ fill: textColor, fontSize: 12 }}
            tickFormatter={(v: string) => truncateLabel(v)}
          />
          <Tooltip contentStyle={tooltipStyle} formatter={formatTooltipValue} />
          <Legend wrapperStyle={{ fontSize: 12, color: textColor }} />
          <Bar dataKey="prompt_tokens" stackId="tokens" name="入力トークン" fill={promptColor} />
          <Bar dataKey="completion_tokens" stackId="tokens" name="出力トークン" fill={completionColor} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
