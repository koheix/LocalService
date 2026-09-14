import { Link } from "react-router-dom";
import { Header } from "../components/Header";

/** まだ実装されていない画面の仮ページ。リンク自体は有効にしておく。 */
export function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <div className="min-h-screen bg-white dark:bg-gray-900">
      <Header />
      <main className="mx-auto flex max-w-[960px] flex-col gap-4 p-6">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">{title}</h1>
        <p className="text-gray-500 dark:text-gray-400">{note}</p>
        <Link to="/" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ホームへ戻る
        </Link>
      </main>
    </div>
  );
}
