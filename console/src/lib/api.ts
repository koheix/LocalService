export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type UnauthorizedHandler = () => void;

const unauthorizedHandlers = new Set<UnauthorizedHandler>();

/** セッション切れ(401)を検知したときに呼ばれるハンドラを登録する。 */
export function onUnauthorized(handler: UnauthorizedHandler): () => void {
  unauthorizedHandlers.add(handler);
  return () => unauthorizedHandlers.delete(handler);
}

type ApiFetchOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | Record<string, unknown>;
};

/**
 * gateway への fetch ラッパー。
 *
 * - Cookie(HttpOnly session)は同一オリジンなので `credentials` 指定不要。
 * - 401 を受けたら(ログインAPI自身を除き)登録済みハンドラを呼び、
 *   呼び出し元には ApiError として投げる。React Router 側の RequireAuth が
 *   認証状態の変化を見てログイン画面へリダイレクトする。
 */
export async function apiFetch<T>(
  path: string,
  options: ApiFetchOptions = {},
  { treatUnauthorizedAsSessionExpiry = true }: { treatUnauthorizedAsSessionExpiry?: boolean } = {},
): Promise<T> {
  const isBodyPlainObject =
    options.body !== undefined &&
    !(options.body instanceof FormData) &&
    typeof options.body !== "string";
  const headers = new Headers(options.headers);
  if (isBodyPlainObject) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, {
    ...options,
    headers,
    body: isBodyPlainObject ? JSON.stringify(options.body) : (options.body as BodyInit | undefined),
  });

  if (res.status === 401 && treatUnauthorizedAsSessionExpiry) {
    for (const handler of unauthorizedHandlers) handler();
  }

  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    let code: string | undefined;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code;
    } catch {
      // レスポンスボディがJSONでない場合はそのまま
    }
    throw new ApiError(res.status, message, code);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}
