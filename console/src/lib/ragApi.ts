import { apiFetch } from "./api";

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

export function ragQuery(question: string): Promise<RagQueryResponse> {
  return apiFetch("/api/rag/query", { method: "POST", body: { question } });
}
