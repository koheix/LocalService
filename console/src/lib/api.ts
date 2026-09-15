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

/**
 * apiFetchを経由しない生fetch(SSEストリーミングなど)が401を検知したときに、
 * apiFetchが401時に行うのと同じ通知を行うためのもの。
 */
export function notifyUnauthorized(): void {
  for (const handler of unauthorizedHandlers) handler();
}

type ApiFetchOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | Record<string, unknown>;
};

type ApiFetchExtra = {
  treatUnauthorizedAsSessionExpiry?: boolean;
  /**
   * ミリ秒でタイムアウトする。gatewayがハング/無応答のとき、fetchが
   * いつまでも解決せず呼び出し元(例: システム状態パネルのポーリング)が
   * 古いデータを表示し続けてしまうのを防ぐ。既定は無制限(タイムアウトしない)。
   */
  timeoutMs?: number;
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
  { treatUnauthorizedAsSessionExpiry = true, timeoutMs }: ApiFetchExtra = {},
): Promise<T> {
  const isBodyPlainObject =
    options.body !== undefined &&
    !(options.body instanceof FormData) &&
    typeof options.body !== "string";
  const headers = new Headers(options.headers);
  if (isBodyPlainObject) {
    headers.set("Content-Type", "application/json");
  }

  // 注意: timeoutMsとoptions.signalを同時に指定した場合、後者は使われない
  // (現状どの呼び出し元も両方を渡していないため実害なし)。両方が必要に
  // なったらAbortSignal.anyでまとめること。
  const timeoutController = timeoutMs !== undefined ? new AbortController() : undefined;
  const timeoutId =
    timeoutController !== undefined
      ? setTimeout(() => timeoutController.abort(), timeoutMs)
      : undefined;

  let res: Response;
  try {
    res = await fetch(path, {
      // gatewayのJSONレスポンスにはCache-Controlが付いていないため、
      // 明示的にno-storeを指定しない限りブラウザのHTTPキャッシュに
      // 保存され得る。ポーリングで「gatewayが落ちているのに直前の
      // 成功レスポンスが再利用され続ける」事故を防ぐため、常に無効化する
      // (確認済み: これを外すと、gateway停止中でもSystemStatusPanelが
      // 古いデータを表示し続けることを実機で確認した)。
      cache: "no-store",
      ...options,
      headers,
      signal: timeoutController?.signal ?? options.signal,
      body: isBodyPlainObject
        ? JSON.stringify(options.body)
        : (options.body as BodyInit | undefined),
    });
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }

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
