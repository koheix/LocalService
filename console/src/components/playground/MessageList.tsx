import { useEffect, useRef } from "react";

export type ChatMessage = { role: "user" | "assistant"; content: string };

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {messages.map((m, i) => (
        <div
          key={i}
          className={
            "max-w-[90%] whitespace-pre-wrap break-words rounded-2xl px-4 py-2 sm:max-w-[70%] " +
            (m.role === "user"
              ? "self-end bg-blue-600 text-white"
              : "self-start bg-gray-200 text-gray-900 dark:bg-gray-700 dark:text-gray-100")
          }
        >
          {m.content}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
