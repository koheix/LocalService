import { FileSearch, Key, Mic, MessageCircle, type LucideIcon } from "lucide-react";
import { Link } from "react-router-dom";

type AppStatus = "available" | "phase2";

type AppDef = {
  name: string;
  description: string;
  icon: LucideIcon;
  status: AppStatus;
  to: string;
};

const APPS: AppDef[] = [
  {
    name: "プレイグラウンド",
    description: "モデルを選んで対話する",
    icon: MessageCircle,
    status: "available",
    to: "/playground",
  },
  {
    name: "社内文書検索",
    description: "資料を読み込んで質問する",
    icon: FileSearch,
    status: "phase2",
    to: "/rag",
  },
  {
    name: "文字起こし",
    description: "録音から議事録を作る",
    icon: Mic,
    status: "phase2",
    to: "/transcription",
  },
  {
    name: "API キー",
    description: "外部ツールから接続する",
    icon: Key,
    status: "available",
    to: "/api-keys",
  },
];

function StatusBadge({ status }: { status: AppStatus }) {
  if (status === "available") {
    return (
      <span className="inline-block rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-700 dark:bg-green-900/50 dark:text-green-300">
        利用可能
      </span>
    );
  }
  return (
    <span className="inline-block rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/50 dark:text-blue-300">
      Phase 2
    </span>
  );
}

export function AppGrid() {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-gray-700 dark:text-gray-300">アプリ</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {APPS.map((app) => {
          const Icon = app.icon;
          const disabled = app.status !== "available";
          const cardClass =
            "flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800" +
            (disabled ? " opacity-50" : " hover:border-gray-300 dark:hover:border-gray-600");

          const content = (
            <>
              <Icon className="h-6 w-6 text-gray-500 dark:text-gray-400" aria-hidden />
              <div>
                <div className="font-medium text-gray-900 dark:text-gray-100">{app.name}</div>
                <div className="text-sm text-gray-500 dark:text-gray-400">{app.description}</div>
              </div>
              <StatusBadge status={app.status} />
            </>
          );

          if (disabled) {
            return (
              <div key={app.name} className={`${cardClass} cursor-default`} aria-disabled="true">
                {content}
              </div>
            );
          }
          return (
            <Link key={app.name} to={app.to} className={cardClass}>
              {content}
            </Link>
          );
        })}
      </div>
    </section>
  );
}
