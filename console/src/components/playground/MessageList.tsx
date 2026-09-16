import { useEffect, useRef } from "react";
import { ThinkingBox } from "../ThinkingBox";

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  /** ストリーミング中の思考内容(reasoning、T-31)。会話履歴の再取得では
   * バックエンドが保存していないため常に空で、ライブ中のみ意味を持つ。 */
  reasoning?: string;
};

export function MessageList({
  messages,
  sending = false,
}: {
  messages: ChatMessage[];
  /** 送信中(応答待ち/ストリーミング中)かどうか。最後のメッセージが
   * assistantのときだけ、生成中インジケータの表示に使う。 */
  sending?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const lastIndex = messages.length - 1;

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {messages.map((m, i) => {
        const isStreamingTarget = sending && i === lastIndex && m.role === "assistant";
        const contentStarted = m.content !== "";
        return (
          <div
            key={i}
            className={"flex flex-col " + (m.role === "user" ? "items-end" : "items-start")}
          >
            {m.role === "assistant" && m.reasoning && (
              <ThinkingBox reasoning={m.reasoning} done={contentStarted} />
            )}
            {/* 思考中(reasoningがあり本文がまだ無い)間はThinkingBoxだけを見せ、
             * 空の吹き出しを重ねて表示しない。 */}
            {!(isStreamingTarget && !contentStarted && m.reasoning) && (
              <div
                className={
                  "max-w-[90%] whitespace-pre-wrap break-words rounded-2xl px-4 py-2 sm:max-w-[70%] " +
                  (m.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-gray-200 text-gray-900 dark:bg-gray-700 dark:text-gray-100")
                }
              >
                {isStreamingTarget && !contentStarted ? (
                  <span role="status" className="italic text-gray-600 dark:text-gray-300">
                    生成中…
                  </span>
                ) : (
                  <>
                    {m.content}
                    {isStreamingTarget && (
                      <span
                        className="ml-0.5 inline-block h-[1em] w-[2px] animate-pulse bg-current align-middle"
                        aria-hidden
                      />
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
