import { Menu } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Header } from "../components/Header";
import { MessageList, type ChatMessage } from "../components/playground/MessageList";
import { Sidebar } from "../components/playground/Sidebar";
import { SettingsPanel, type SettingsFormState } from "../components/playground/SettingsPanel";
import {
  createConversation,
  getConversation,
  listConversations,
  listMessages,
  listModels,
  sendMessageStream,
  updateConversation,
  type Conversation,
  type ModelOut,
} from "../lib/conversationsApi";

const DEFAULT_FORM: SettingsFormState = {
  title: "",
  model: "",
  systemPrompt: "",
  temperature: "0.7",
  topP: "1",
  maxTokens: "",
};

function conversationToForm(c: Conversation): SettingsFormState {
  return {
    title: c.title,
    model: c.model ?? "",
    systemPrompt: c.system_prompt,
    temperature: String(c.temperature),
    topP: String(c.top_p),
    maxTokens: c.max_tokens === null ? "" : String(c.max_tokens),
  };
}

/**
 * プレイグラウンド(React版、T-21)。Vanilla JS版(T-14, caddy/console/)と
 * 同等の操作(モデル選択・システムプロンプト編集・temperature/top_p/
 * max_tokens調整・ストリーミング表示・履歴保存)を提供する。
 */
export function Playground() {
  const [models, setModels] = useState<ModelOut[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [form, setForm] = useState<SettingsFormState>(DEFAULT_FORM);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const refreshConversations = useCallback(async () => {
    const list = await listConversations();
    setConversations(list);
    return list;
  }, []);

  useEffect(() => {
    (async () => {
      const [modelsRes, convList] = await Promise.all([listModels(), refreshConversations()]);
      // embeddingモデルはチャットに使えないため選択肢から外す。/api/v1/modelsは
      // OpenAI互換の都合上kindを問わず全モデルを返すため、ここで絞り込む
      // (絞らないと、新規会話のデフォルトモデルがembeddingモデルになりうる)。
      setModels(modelsRes.data.filter((m) => m.kind === "chat"));
      if (convList.length > 0) {
        await selectConversation(convList[0].id);
      } else {
        // 会話が1件も無いと「新しい会話を始める」ボタンがドロワーの中に
        // 隠れて見えなくなる(sm未満)。狭い画面では開いておく。
        setSidebarOpen(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function selectConversation(id: number) {
    if (sending) return;
    setCurrentId(id);
    setSidebarOpen(false); // モバイルでは選択したら閉じてチャット画面を見せる
    const [conv, msgs] = await Promise.all([getConversation(id), listMessages(id)]);
    setForm(conversationToForm(conv));
    setMessages(
      msgs
        .filter((m): m is typeof m & { role: "user" | "assistant" } => m.role !== "system")
        .map((m) => ({ role: m.role, content: m.content })),
    );
  }

  async function handleCreate() {
    if (sending) return;
    const defaultModel = models[0]?.id ?? null;
    const created = await createConversation("新しい会話", defaultModel);
    await refreshConversations();
    await selectConversation(created.id);
  }

  async function handleSaveSettings() {
    if (currentId === null) return;
    await updateConversation(currentId, {
      title: form.title,
      model: form.model || null,
      system_prompt: form.systemPrompt,
      temperature: Number(form.temperature),
      top_p: Number(form.topP),
      max_tokens: form.maxTokens.trim() === "" ? null : Number(form.maxTokens),
    });
    await refreshConversations();
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || currentId === null || sending) return;
    setInput("");
    setSending(true);
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);

    try {
      await sendMessageStream(currentId, text, (delta) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, content: last.content + delta };
          return next;
        });
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "送信に失敗しました";
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: "assistant", content: `[エラー] ${message}` };
        return next;
      });
    } finally {
      setSending(false);
      void refreshConversations();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      (e.currentTarget.form as HTMLFormElement | null)?.requestSubmit();
    }
  }

  return (
    <div className="flex h-screen flex-col bg-white dark:bg-gray-900">
      <Header />
      <div className="flex min-h-0 flex-1">
        <Sidebar
          conversations={conversations}
          currentId={currentId}
          onSelect={selectConversation}
          onCreate={handleCreate}
          disabled={sending}
          open={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          {/* サイドバーが幅の狭い画面(sm未満)では隠れているため、開閉と現在の
              会話タイトルを確認できる帯をここに出す。sm以上ではサイドバーが
              常時見えているので不要。 */}
          <div className="flex items-center gap-2 border-b border-gray-200 p-2 dark:border-gray-700 sm:hidden">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              aria-label="会話一覧を開く"
              aria-expanded={sidebarOpen}
              aria-controls="playground-sidebar"
              className="rounded-lg p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              <Menu className="h-5 w-5" aria-hidden />
            </button>
            <span className="truncate text-sm text-gray-700 dark:text-gray-300">
              {currentId === null
                ? "会話を選択してください"
                : (conversations.find((c) => c.id === currentId)?.title ?? `(無題 #${currentId})`)}
            </span>
          </div>
          {currentId === null ? (
            <div className="m-auto text-gray-500 dark:text-gray-400">
              「新しい会話を始める」から始めてください。
            </div>
          ) : (
            <>
              <SettingsPanel models={models} form={form} onChange={setForm} onSave={handleSaveSettings} />
              <MessageList messages={messages} />
              <form onSubmit={handleSend} className="flex gap-2 border-t border-gray-200 p-3 dark:border-gray-700">
                <textarea
                  rows={2}
                  required
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="メッセージを入力（Shift+Enterで改行、Enterで送信）"
                  className="flex-1 resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
                />
                <button
                  type="submit"
                  disabled={sending}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  送信する
                </button>
              </form>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
