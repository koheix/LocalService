import { useState, type FormEvent } from "react";
import { ragQueryStream, type Citation } from "../../lib/ragApi";
import { ThinkingBox } from "../ThinkingBox";

/** idle以外は質問中。searching→(関連文書があれば)generatingと遷移する(T-27)。 */
type Phase = "idle" | "searching" | "generating";

export function QuestionPanel() {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [answer, setAnswer] = useState<string | null>(null);
  const [reasoning, setReasoning] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || phase !== "idle") return;
    setPhase("searching");
    setAnswer(null);
    setReasoning("");
    setCitations([]);
    let receivedAnyDelta = false;
    try {
      await ragQueryStream(q, {
        onGenerating: () => setPhase("generating"),
        onReasoning: (text) => setReasoning((prev) => prev + text),
        onDelta: (text) => {
          receivedAnyDelta = true;
          setAnswer((prev) => (prev ?? "") + text);
        },
        onCitations: setCitations,
      });
      // バックエンドがdeltaを1つも送らずに完走することがある(意図的な
      // 仕様。gateway/tests/test_rag.pyのtest_rag_query_stream_completes_with_empty_answer
      // 参照)。answerがnullのままだと画面の描画条件(asking || answer !== null)が
      // 偽に戻り、質問しても何も表示されないまま終わったように見えてしまうため、
      // その場合だけ明示的なプレースホルダーを出す。
      if (!receivedAnyDelta) {
        setAnswer("(回答が空でした)");
      }
    } catch (err) {
      // 途中まで届いていた本文があれば残し、末尾にエラーを追記する
      // (せっかくストリーミングされた分を丸ごと消さない)。
      const message = err instanceof Error ? err.message : "質問に失敗しました";
      setAnswer((prev) => (prev ? `${prev}\n[エラー] ${message}` : `[エラー] ${message}`));
    } finally {
      setPhase("idle");
    }
  }

  const asking = phase !== "idle";

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

      {(asking || answer !== null) && (
        <div className="flex flex-col gap-3 border-t border-gray-200 pt-3 dark:border-gray-700">
          <div>
            <h3 className="mb-1 text-xs font-semibold text-gray-500 dark:text-gray-400">回答</h3>
            {/* ThinkingBoxはanswerの有無で分岐させない(常に同じ位置・同じ
             * コンポーネント型で描画する)。三項演算子の別々の分岐に置くと、
             * answerがnull→非nullに変わった瞬間にReactが別インスタンスとして
             * 再マウントしてしまい、ThinkingBox内部の「done化した瞬間だけ
             * 自動で畳む」ロジックが働かなくなる(マウント時点で既にdone=true
             * になっているため)。 */}
            {reasoning && <ThinkingBox reasoning={reasoning} done={answer !== null} />}
            {answer === null ? (
              !reasoning && (
                <span role="status" className="text-sm italic text-gray-600 dark:text-gray-300">
                  {phase === "searching" ? "検索中…" : "回答を生成中…"}
                </span>
              )
            ) : (
              <p className="whitespace-pre-wrap text-sm text-gray-900 dark:text-gray-100">
                {answer}
                {asking && (
                  <span
                    className="ml-0.5 inline-block h-[1em] w-[2px] animate-pulse bg-current align-middle"
                    aria-hidden
                  />
                )}
              </p>
            )}
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
