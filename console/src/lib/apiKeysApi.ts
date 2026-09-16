import { apiFetch } from "./api";

export type ApiKeyOut = {
  id: number;
  name: string;
  prefix: string;
  last_used_at: string | null;
  created_at: string;
};

export type ApiKeyCreated = {
  id: number;
  name: string;
  key: string;
  prefix: string;
};

export function listApiKeys(): Promise<ApiKeyOut[]> {
  return apiFetch("/api/auth/api-keys");
}

export function createApiKey(name: string): Promise<ApiKeyCreated> {
  return apiFetch("/api/auth/api-keys", { method: "POST", body: { name } });
}

export function revokeApiKey(id: number): Promise<void> {
  return apiFetch(`/api/auth/api-keys/${id}`, { method: "DELETE" });
}
