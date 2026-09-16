import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * ストリーミング中のreasoning(思考内容)をライブ表示する(T-31)。
 *
 * chat-standard(qwen3:4b)はOllama側のテンプレート設定で応答生成のたびに
 * 強制的に長い思考を行い、これはAPIパラメータでは止められない(T-26で
 * 発覚)。この待ち時間を「固まっている」ように見せないよう、思考内容
 * そのものをリアルタイムに見せる。`content`(本文)が出始めたら
 * `done=true`になり、自動的に折りたたむ(ユーザーが再度展開できる)。
 */
export function ThinkingBox({ reasoning, done }: { reasoning: string; done: boolean }) {
  const [expanded, setExpanded] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const wasDoneRef = useRef(done);

  useEffect(() => {
    // doneが false→true に変わった瞬間(本文が出始めた瞬間)だけ自動で
    // 折りたたむ。ユーザーが手動で開いた状態を、その後の再レンダーで
    // 勝手に戻したりはしない。
    if (done && !wasDoneRef.current) {
      setExpanded(false);
    }
    wasDoneRef.current = done;
  }, [done]);

  useEffect(() => {
    if (expanded && !done) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [reasoning, expanded, done]);

  if (!reasoning) return null;

  return (
    <div className="mb-1 max-w-[90%] rounded-lg border border-gray-200 bg-gray-50 text-xs dark:border-gray-700 dark:bg-gray-900/40 sm:max-w-[70%]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between gap-2 px-2 py-1 text-gray-500 dark:text-gray-400"
      >
        <span className={done ? undefined : "italic"}>
          {done ? "思考過程を見る" : "思考中…"}
        </span>
        <ChevronDown
          className={`h-3 w-3 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {expanded && (
        <div className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words px-2 pb-2 text-gray-500 dark:text-gray-400">
          {reasoning}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}
