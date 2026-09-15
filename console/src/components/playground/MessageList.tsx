import { useEffect, useRef } from "react";

export type ChatMessage = { role: "user" | "assistant"; content: string };

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
        return (
          <div
            key={i}
            className={
              "max-w-[90%] whitespace-pre-wrap break-words rounded-2xl px-4 py-2 sm:max-w-[70%] " +
              (m.role === "user"
                ? "self-end bg-blue-600 text-white"
                : "self-start bg-gray-200 text-gray-900 dark:bg-gray-700 dark:text-gray-100")
            }
          >
            {isStreamingTarget && m.content === "" ? (
              <span className="italic text-gray-500 dark:text-gray-400">生成中…</span>
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
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
