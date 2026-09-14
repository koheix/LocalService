import { useState } from "react";
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
  onSave: () => void;
}) {
  const [saved, setSaved] = useState(false);

  function set<K extends keyof SettingsFormState>(key: K, value: SettingsFormState[K]) {
    onChange({ ...form, [key]: value });
  }

  function handleSave() {
    onSave();
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
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
        </div>
      </div>
    </details>
  );
}
