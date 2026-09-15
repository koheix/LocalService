import { useRef, useState, type FormEvent } from "react";
import { useAuth } from "../../context/useAuth";
import { ApiError } from "../../lib/api";
import { deleteDocument, uploadDocument, type DocumentOut } from "../../lib/ragApi";

const STATUS_LABEL: Record<DocumentOut["status"], string> = {
  pending: "待機中",
  indexing: "解析中",
  ready: "準備完了",
  failed: "失敗",
};

const STATUS_CLASS: Record<DocumentOut["status"], string> = {
  pending: "text-amber-600 dark:text-amber-400",
  indexing: "text-amber-600 dark:text-amber-400",
  ready: "text-green-700 dark:text-green-400",
  failed: "text-red-600 dark:text-red-400",
};

export function DocumentPanel({
  documents,
  onChanged,
}: {
  documents: DocumentOut[];
  onChanged: () => void;
}) {
  const { user } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  async function handleUpload(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await uploadDocument(file);
      if (fileInputRef.current) fileInputRef.current.value = "";
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "アップロードに失敗しました");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await deleteDocument(id);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "削除に失敗しました");
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800">
      <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
        社内文書（全ユーザー共有）
      </h2>
      <form onSubmit={handleUpload} className="flex flex-wrap gap-2">
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.txt"
          required
          className="min-w-[160px] flex-1 text-sm text-gray-700 dark:text-gray-300"
        />
        <button
          type="submit"
          disabled={uploading}
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          アップロードする
        </button>
      </form>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto">
        {documents.map((d) => (
          <li
            key={d.id}
            className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700"
          >
            <span className="flex-1 truncate text-gray-900 dark:text-gray-100">{d.filename}</span>
            <span className={`whitespace-nowrap text-xs ${STATUS_CLASS[d.status]}`}>
              {STATUS_LABEL[d.status]}
            </span>
            {(d.owner_id === user?.id || user?.role === "admin") && (
              <button
                type="button"
                onClick={() => void handleDelete(d.id)}
                className="text-xs text-red-600 hover:underline dark:text-red-400"
              >
                削除する
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
