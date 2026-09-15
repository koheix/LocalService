import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import type { ModelOut } from "../../lib/conversationsApi";

export type SettingsFormState = {
  title: string;
  model: string;
  systemPrompt: string;
  temperature: string;
  topP: string;
  maxTokens: string;
};

export function SettingsPanel({
  models,
  form,
  onChange,
  onSave,
}: {
  models: ModelOut[];
  form: SettingsFormState;
  onChange: (form: SettingsFormState) => void;
  onSave: () => Promise<void>;
}) {
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    };
  }, []);

  function set<K extends keyof SettingsFormState>(key: K, value: SettingsFormState[K]) {
    onChange({ ...form, [key]: value });
  }

  async function handleSave() {
    setError(null);
    try {
      await onSave();
      setSaved(true);
      savedTimerRef.current = setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "設定の保存に失敗しました");
    }
  }

  return (
    <details className="border-b border-gray-200 px-4 py-2 dark:border-gray-700" open>
      <summary className="cursor-pointer text-sm text-gray-500 dark:text-gray-400">
        会話設定
      </summary>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          タイトル
          <input
            type="text"
            value={form.title}
            onChange={(e) => set("title", e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          モデル
          <select
            value={form.model}
            onChange={(e) => set("model", e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          >
            {/* 会話が保持しているモデル(embeddingへの変更・無効化等で選択肢
                から消えた場合を含む)が一覧に無いと、ブラウザは先頭optionを
                表示するのにform.modelは古い値のままという表示とstateの
                食い違いが起きる。実際の値をそのまま出す選択肢を足しておく。 */}
            {form.model && !models.some((m) => m.id === form.model) && (
              <option value={form.model}>{form.model}（選択できないモデル）</option>
            )}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          temperature
          <input
            type="number"
            step="0.1"
            min="0"
            max="2"
            value={form.temperature}
            onChange={(e) => set("temperature", e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          top_p
          <input
            type="number"
            step="0.05"
            min="0"
            max="1"
            value={form.topP}
            onChange={(e) => set("topP", e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          max_tokens（空欄=既定）
          <input
            type="number"
            min="1"
            value={form.maxTokens}
            onChange={(e) => set("maxTokens", e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300 sm:col-span-2 lg:col-span-3">
          system prompt
          <textarea
            rows={3}
            value={form.systemPrompt}
            onChange={(e) => set("systemPrompt", e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />
        </label>
        <div className="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
          <button
            type="button"
            onClick={handleSave}
            className="rounded-lg bg-gray-200 px-3 py-1.5 text-sm text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-100 dark:hover:bg-gray-600"
          >
            設定を保存する
          </button>
          {saved && <span className="text-xs text-gray-500 dark:text-gray-400">保存しました</span>}
          {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
        </div>
      </div>
    </details>
  );
}
