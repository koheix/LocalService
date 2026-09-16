import { Component, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { hasError: boolean };

/**
 * React.lazyの動的import()失敗(再デプロイ後、開きっぱなしのタブが
 * ハッシュ付きチャンクを404で取得できない等)をSPA全体の白画面クラッシュに
 * しないための最小限のErrorBoundary(T-29)。React.lazyを使う箇所でのみ
 * 使う想定で、アプリ全体を覆う汎用ErrorBoundaryではない。
 */
export class LazyLoadErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: { componentStack?: string | null }): void {
    // このboundaryはimport()失敗以外の例外(想定外のデータでchartがthrowする
    // 等)も同じ画面で覆ってしまうため、原因調査ができるよう握り潰さず
    // ログに残す(再読み込みしても直らないバグを「読み込み失敗」だと
    // 誤解させないため)。
    console.error("LazyLoadErrorBoundary caught an error", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-white p-4 dark:bg-gray-900">
          <div className="text-center text-sm text-gray-600 dark:text-gray-300">
            <p className="mb-3">画面の読み込みに失敗しました。</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700"
            >
              再読み込み
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
