import { useState, type FormEvent } from "react";
import { ragQuery, type Citation } from "../../lib/ragApi";

export function QuestionPanel() {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setAnswer("考え中...");
    setCitations([]);
    try {
      const res = await ragQuery(q);
      setAnswer(res.answer);
      setCitations(res.citations);
    } catch (err) {
      setAnswer(`[エラー] ${err instanceof Error ? err.message : "質問に失敗しました"}`);
    } finally {
      setAsking(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800">
      <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
        文書について質問する
      </h2>
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <textarea
          rows={2}
          required
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="例: 検証機のGPUは何ですか？"
          className="resize-y rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
        />
        <button
          type="submit"
          disabled={asking}
          className="self-start rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          質問する
        </button>
      </form>

      {answer !== null && (
        <div className="flex flex-col gap-3 border-t border-gray-200 pt-3 dark:border-gray-700">
          <div>
            <h3 className="mb-1 text-xs font-semibold text-gray-500 dark:text-gray-400">回答</h3>
            <p className="whitespace-pre-wrap text-sm text-gray-900 dark:text-gray-100">{answer}</p>
          </div>
          {citations.length > 0 && (
            <div className="flex flex-col gap-2">
              {citations.map((c) => (
                <div
                  key={c.chunk_id}
                  className="rounded-lg bg-gray-100 px-3 py-2 text-xs dark:bg-gray-900/60"
                >
                  <div className="mb-1 text-gray-500 dark:text-gray-400">出典: {c.filename}</div>
                  <div className="text-gray-700 dark:text-gray-300">{c.content}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
