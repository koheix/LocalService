import { useCallback, useEffect, useRef, useState } from "react";
import { Header } from "../components/Header";
import { DocumentPanel } from "../components/rag/DocumentPanel";
import { QuestionPanel } from "../components/rag/QuestionPanel";
import { listDocuments, type DocumentOut } from "../lib/ragApi";

const POLL_INTERVAL_MS = 3_000;

/**
 * 社内文書検索(React版、T-22)。Vanilla JS版(T-18, caddy/console/)と
 * 同等の操作(アップロード・一覧・削除・質問と引用付き回答)を提供する。
 */
export function Rag() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    const docs = await listDocuments();
    setDocuments(docs);
    const hasInFlight = docs.some((d) => d.status === "pending" || d.status === "indexing");
    if (hasInFlight) {
      if (!pollTimerRef.current) {
        pollTimerRef.current = setInterval(() => void refresh(), POLL_INTERVAL_MS);
      }
    } else if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen bg-white dark:bg-gray-900">
      <Header />
      <main className="mx-auto grid max-w-[960px] grid-cols-1 gap-6 p-4 sm:p-6 lg:grid-cols-2">
        <DocumentPanel documents={documents} onChanged={refresh} />
        <QuestionPanel />
      </main>
    </div>
  );
}
