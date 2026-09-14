import { AdminSection } from "../components/AdminSection";
import { AppGrid } from "../components/AppGrid";
import { Header } from "../components/Header";
import { SystemStatusPanel } from "../components/SystemStatusPanel";
import { useAuth } from "../context/useAuth";

/** ログイン後に最初に表示される画面(docs/UI_HOME.md)。チャット入力欄は置かない。 */
export function Home() {
  const { user } = useAuth();

  return (
    <div className="min-h-screen bg-white dark:bg-gray-900">
      <Header />
      <main className="mx-auto flex max-w-[960px] flex-col gap-8 p-4 sm:p-6">
        <SystemStatusPanel />
        <AppGrid />
        {user?.role === "admin" && <AdminSection />}
      </main>
    </div>
  );
}
