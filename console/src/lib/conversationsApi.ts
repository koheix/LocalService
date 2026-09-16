import { apiFetch, ApiError, notifyUnauthorized } from "./api";

export type ModelOut = {
  id: string;
  object: string;
  owned_by: string;
  kind: "chat" | "embedding";
};

export type Conversation = {
  id: number;
  title: string;
  model: string | null;
  system_prompt: string;
  temperature: number;
  top_p: number;
  max_tokens: number | null;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: number;
  role: "system" | "user" | "assistant";
  content: string;
  created_at: string;
};

export type ConversationUpdate = {
  title?: string;
  model?: string | null;
  system_prompt?: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number | null;
};

export function listModels(): Promise<{ data: ModelOut[] }> {
  return apiFetch("/api/v1/models");
}

export function listConversations(): Promise<Conversation[]> {
  return apiFetch("/api/conversations");
}

export function createConversation(title: string, model: string | null): Promise<Conversation> {
  return apiFetch("/api/conversations", { method: "POST", body: { title, model } });
}

export function getConversation(id: number): Promise<Conversation> {
  return apiFetch(`/api/conversations/${id}`);
}

export function updateConversation(id: number, body: ConversationUpdate): Promise<Conversation> {
  return apiFetch(`/api/conversations/${id}`, { method: "PATCH", body });
}

export function listMessages(id: number): Promise<Message[]> {
  return apiFetch(`/api/conversations/${id}/messages`);
}

export type SendMessageHandlers = {
  /** 本文(content)の断片を受け取るたびに呼ばれる。 */
  onDelta: (text: string) => void;
  /** 思考内容(reasoning、T-31)の断片を受け取るたびに呼ばれる。
   * chat-standard(qwen3:4b)のように思考モードを持つモデルのときだけ
   * 呼ばれる(持たないモデルではreasoningフィールド自体が来ない)。 */
  onReasoning?: (text: string) => void;
};

/**
 * メッセージ送信(SSEストリーミング)。生のfetchを使う(EventSourceはPOST不可、
 * apiFetchはJSONを一括で返す前提のため、この用途には向かない)。
 */
export async function sendMessageStream(
  conversationId: number,
  content: string,
  handlers: SendMessageHandlers,
): Promise<void> {
  const res = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({ content }),
  });

  if (!res.ok || !res.body) {
    if (res.status === 401) notifyUnauthorized();
    let message = `HTTP ${res.status}`;
    let code: string | undefined;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code;
    } catch {
      // ボディがJSONでない場合はそのまま
    }
    throw new ApiError(res.status, message, code);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice("data: ".length);
      if (payload === "[DONE]") continue;
      try {
        const obj = JSON.parse(payload);
        const delta = obj.choices?.[0]?.delta ?? {};
        if (delta.reasoning) handlers.onReasoning?.(delta.reasoning);
        if (delta.content) handlers.onDelta(delta.content);
      } catch {
        // 断片的なJSONは無視(チャンク境界がSSEイベント境界と一致しない場合がある)
      }
    }
  }
}
