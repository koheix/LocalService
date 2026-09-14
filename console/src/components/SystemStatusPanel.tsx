import { useEffect, useState } from "react";
import { fetchGpu, fetchHealth, type GpuResponse, type HealthResponse } from "../lib/adminApi";

const POLL_INTERVAL_MS = 30_000;

type State = {
  loading: boolean;
  health: HealthResponse | null;
  gpu: GpuResponse | null;
  reachable: boolean;
};

function barColorClass(pct: number): string {
  if (pct >= 85) return "bg-red-500";
  if (pct >= 70) return "bg-amber-500";
  return "bg-blue-500";
}

export function SystemStatusPanel() {
  const [state, setState] = useState<State>({
    loading: true,
    health: null,
    gpu: null,
    reachable: true,
  });

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const [health, gpu] = await Promise.all([fetchHealth(), fetchGpu()]);
        if (cancelled) return;
        setState({ loading: false, health, gpu, reachable: true });
      } catch {
        // APIが落ちている場合でも画面全体を壊さない。ドットを赤にし数値は"—"にする。
        if (cancelled) return;
        setState((prev) => ({ ...prev, loading: false, reachable: false }));
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const gpuInfo = state.reachable && state.gpu?.available ? state.gpu : null;
  const loadedModels = state.reachable ? (state.health?.llm.loaded_models ?? []) : [];
  const usagePct =
    gpuInfo && gpuInfo.memory_total_mb > 0
      ? Math.round((gpuInfo.memory_used_mb / gpuInfo.memory_total_mb) * 100)
      : 0;

  return (
    <section className="rounded-xl bg-gray-100 p-5 dark:bg-gray-800/60">
      {state.loading ? (
        <div className="flex flex-col gap-3 animate-pulse">
          <div className="h-4 w-40 rounded bg-gray-300 dark:bg-gray-700" />
          <div className="h-2 w-full rounded bg-gray-300 dark:bg-gray-700" />
          <div className="h-4 w-56 rounded bg-gray-300 dark:bg-gray-700" />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span
                className={`h-2.5 w-2.5 rounded-full ${state.reachable ? "bg-green-500" : "bg-red-500"}`}
                aria-hidden
              />
              <span className="text-sm font-medium text-gray-800 dark:text-gray-100">
                {state.reachable ? "システム稼働中" : "システムに接続できません"}
              </span>
            </div>
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {gpuInfo ? gpuInfo.name : "—"}
            </span>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between text-sm text-gray-500 dark:text-gray-400">
              <span>VRAM</span>
              <span className="font-mono">
                {gpuInfo
                  ? `${(gpuInfo.memory_used_mb / 1024).toFixed(1)} / ${(gpuInfo.memory_total_mb / 1024).toFixed(1)} GB`
                  : "—"}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-gray-300 dark:bg-gray-700">
              {gpuInfo && (
                <div
                  className={`h-full rounded-full ${barColorClass(usagePct)}`}
                  style={{ width: `${Math.min(100, usagePct)}%` }}
                />
              )}
            </div>
          </div>

          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-500 dark:text-gray-400">読み込み中のモデル</span>
            <span className="font-mono text-gray-800 dark:text-gray-100">
              {!state.reachable ? "—" : loadedModels.length > 0 ? loadedModels.join(", ") : "モデル未読み込み"}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
