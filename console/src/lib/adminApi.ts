import { apiFetch } from "./api";

export type HealthResponse = {
  llm: { ok: boolean; url: string; loaded_models: string[]; detail?: string };
  embed: { ok: boolean; url: string; loaded_models?: string[]; detail?: string };
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
