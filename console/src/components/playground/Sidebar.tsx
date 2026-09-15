import { Plus, X } from "lucide-react";
import { useEffect } from "react";
import type { Conversation } from "../../lib/conversationsApi";

export function Sidebar({
  conversations,
  currentId,
  onSelect,
  onCreate,
  disabled = false,
  open = false,
  onClose,
}: {
  conversations: Conversation[];
  currentId: number | null;
  onSelect: (id: number) => void;
  onCreate: () => void;
  disabled?: boolean;
  /** モバイル(sm未満)でオーバーレイ表示するかどうか。sm以上では常に表示する。 */
  open?: boolean;
  onClose?: () => void;
}) {
  // Header.tsxのユーザーメニューと同様、Escで閉じられるようにする。
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <>
      {/* モバイルでサイドバーを開いているときの背景オーバーレイ。タップで閉じる。 */}
      {open && (
        <div
          className="fixed inset-0 z-20 bg-black/30 sm:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}
      <aside
        id="playground-sidebar"
        role="dialog"
        aria-label="会話一覧"
        aria-modal={open}
        className={
          "flex w-64 flex-none flex-col gap-3 border-r border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/40 " +
          "fixed inset-y-0 left-0 z-30 transition-transform duration-200 sm:static sm:z-auto sm:translate-x-0 " +
          (open ? "translate-x-0" : "-translate-x-full")
        }
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onCreate}
            disabled={disabled}
            className="flex flex-1 items-center justify-center gap-1 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            <Plus className="h-4 w-4" aria-hidden />
            新しい会話を始める
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="会話一覧を閉じる"
            className="rounded-lg p-2 text-gray-500 hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-800 sm:hidden"
          >
            <X className="h-5 w-5" aria-hidden />
          </button>
        </div>
        <ul className="flex flex-1 flex-col gap-1 overflow-y-auto">
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                disabled={disabled}
                className={
                  "w-full truncate rounded-lg px-3 py-2 text-left text-sm disabled:cursor-not-allowed disabled:opacity-50 " +
                  (c.id === currentId
                    ? "bg-blue-600 text-white"
                    : "text-gray-700 hover:bg-gray-200 dark:text-gray-200 dark:hover:bg-gray-800")
                }
              >
                {c.title || `(無題 #${c.id})`}
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </>
  );
}
