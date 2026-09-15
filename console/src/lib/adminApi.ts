import { apiFetch } from "./api";

// admin以外には url/detail(運用情報)を含まない縮小版が返る(gateway側で
// role別に出し分けている)。ここではロール共通で必ず存在するフィールドのみ
// 必須にし、url/detailはadmin向けのオプショナル項目として扱う。
export type HealthResponse = {
  llm: { ok: boolean; loaded_models: string[]; url?: string; detail?: string };
  embed: { ok: boolean; loaded_models?: string[]; url?: string; detail?: string };
  db: { ok: boolean };
};

export type GpuResponse =
  | {
      available: true;
      name: string;
      memory_total_mb: number;
      memory_used_mb: number;
      utilization_pct: number;
      temperature_c: number;
    }
  | { available: false };

export type SummaryResponse = {
  active_user_count: number;
  active_model_count: number;
  today_total_tokens: number;
};

// システム状態パネルのポーリングがgatewayの無応答でいつまでも待ち続けない
// ようにするタイムアウト(docs/UI_HOME.md: APIが落ちている場合はドットを赤に
// し画面を壊さない、を「無応答」のケースでも満たすため)。
const HEALTH_POLL_TIMEOUT_MS = 8_000;

// /health と /gpu は認証済みなら誰でも呼べる(admin限定ではない)。
// docs/UI_HOME.md: ホーム画面のシステム状態パネルは一般ユーザーにも表示するため。
export function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/api/admin/health", {}, { timeoutMs: HEALTH_POLL_TIMEOUT_MS });
}

export function fetchGpu(): Promise<GpuResponse> {
  return apiFetch<GpuResponse>("/api/admin/gpu", {}, { timeoutMs: HEALTH_POLL_TIMEOUT_MS });
}

// /summary はadmin限定。呼び出し側(管理セクション)がuser.role==="admin"のときだけ呼ぶこと。
export function fetchSummary(): Promise<SummaryResponse> {
  return apiFetch<SummaryResponse>("/api/admin/summary");
}

export type UsageGroupBy = "day" | "user" | "model";

// group_byに応じて day/user/model のいずれか1つだけが入る(gateway側の実装参照)。
export type UsageRow = {
  day?: string;
  // usage_logs.user_id/model_idはON DELETE SET NULLなので、削除済みの
  // ユーザー/モデル分の行はnullになり得る。
  user?: number | null;
  model?: number | null;
  request_count: number;
  prompt_tokens: number;
  completion_tokens: number;
};

export type UsageResponse = {
  group_by: UsageGroupBy;
  data: UsageRow[];
};

// /usage はadmin限定。fromToはISO 8601文字列(タイムゾーン省略時はUTC扱いに
// なるため、呼び出し側で明示するかUTC前提で扱うこと)。
export function fetchUsage(params: {
  from: string;
  to: string;
  group_by: UsageGroupBy;
  user_id?: number;
}): Promise<UsageResponse> {
  const search = new URLSearchParams({
    from: params.from,
    to: params.to,
    group_by: params.group_by,
  });
  if (params.user_id !== undefined) search.set("user_id", String(params.user_id));
  return apiFetch<UsageResponse>(`/api/admin/usage?${search.toString()}`);
}

export type UserOut = {
  id: number;
  email: string;
  display_name: string;
  role: string;
  is_active: boolean;
  created_at: string;
};

export type ModelOut = {
  id: number;
  served_name: string;
  backend: string;
  backend_name: string;
  kind: string;
  num_ctx: number;
  is_enabled: boolean;
  description: string;
  permitted_roles: string[];
};

// 利用状況画面でuser_id/model_idを表示名に解決するためだけに使う
// (ユーザー管理・モデル管理画面自体は別タスク)。
export function listUsers(): Promise<UserOut[]> {
  return apiFetch<UserOut[]>("/api/admin/users");
}

export function listModels(): Promise<ModelOut[]> {
  return apiFetch<ModelOut[]>("/api/admin/models");
}
