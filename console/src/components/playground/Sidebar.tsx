import { Plus } from "lucide-react";
import type { Conversation } from "../../lib/conversationsApi";

export function Sidebar({
  conversations,
  currentId,
  onSelect,
  onCreate,
}: {
  conversations: Conversation[];
  currentId: number | null;
  onSelect: (id: number) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="flex w-64 flex-none flex-col gap-3 border-r border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/40">
      <button
        type="button"
        onClick={onCreate}
        className="flex items-center justify-center gap-1 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
      >
        <Plus className="h-4 w-4" aria-hidden />
        新しい会話を始める
      </button>
      <ul className="flex flex-1 flex-col gap-1 overflow-y-auto">
        {conversations.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onSelect(c.id)}
              className={
                "w-full truncate rounded-lg px-3 py-2 text-left text-sm " +
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
  );
}
