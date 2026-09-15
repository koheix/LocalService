import { apiFetch, ApiError, notifyUnauthorized } from "./api";

export type DocumentStatus = "pending" | "indexing" | "ready" | "failed";

export type DocumentOut = {
  id: number;
  owner_id: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  created_at: string;
};

export type Citation = {
  document_id: number;
  filename: string;
  chunk_id: number;
  content: string;
};

export type RagQueryResponse = {
  answer: string;
  citations: Citation[];
};

export function listDocuments(): Promise<DocumentOut[]> {
  return apiFetch("/api/documents");
}

export function uploadDocument(file: File): Promise<DocumentOut> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch("/api/documents", { method: "POST", body: formData });
}

export function deleteDocument(id: number): Promise<void> {
  return apiFetch(`/api/documents/${id}`, { method: "DELETE" });
}

export type RagStreamHandlers = {
  /** 検索が終わり、回答生成(ストリーミング)が始まったときに呼ばれる(T-27)。 */
  onGenerating?: () => void;
  /** 回答本文の断片を受け取るたびに呼ばれる。 */
  onDelta: (text: string) => void;
  /** 引用一覧が確定したときに呼ばれる(回答本文がすべて届いた後、最後に1回)。 */
  onCitations: (citations: Citation[]) => void;
};

/**
 * 質問応答(T-27でSSEストリーミング対応)。
 *
 * 関連する文書チャンクが無い場合、gatewayはLLMを呼ばず即座にJSON応答を返す
 * (`Content-Type: application/json`)。関連チャンクがある場合のみ
 * `text/event-stream`でstatus→delta(複数回)→citationsの順にイベントが届く。
 * 呼び出し元は`Content-Type`の違いを意識せず、同じhandlersで両方を扱える。
 */
export async function ragQueryStream(
  question: string,
  handlers: RagStreamHandlers,
): Promise<void> {
  const res = await fetch("/api/rag/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({ question }),
  });

  if (!res.ok) {
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

  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.startsWith("text/event-stream")) {
    // 関連文書なし(またはそれ以外の非ストリーミング応答): 即時JSON。
    const body = (await res.json()) as RagQueryResponse;
    if (body.answer) handlers.onDelta(body.answer);
    handlers.onCitations(body.citations);
    return;
  }

  if (!res.body) throw new Error("ストリーミング応答の読み取りに失敗しました");

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
      let event: { type?: string; phase?: string; content?: string; citations?: Citation[] };
      try {
        event = JSON.parse(payload);
      } catch {
        continue; // 断片的なJSONは無視(チャンク境界がSSEイベント境界と一致しない場合がある)
      }
      if (event.type === "status" && event.phase === "generating") {
        handlers.onGenerating?.();
      } else if (event.type === "delta" && event.content) {
        handlers.onDelta(event.content);
      } else if (event.type === "citations" && event.citations) {
        handlers.onCitations(event.citations);
      }
    }
  }
}
