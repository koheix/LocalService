import { Cpu } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/useAuth";

function initialOf(email: string): string {
  return email.trim().charAt(0).toUpperCase() || "?";
}

export function Header() {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [menuOpen]);

  if (!user) return null;

  return (
    <header className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
      <div className="flex items-center gap-2">
        <Cpu className="h-6 w-6 text-blue-600 dark:text-blue-400" aria-hidden />
        <span className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          LLM Console
        </span>
      </div>

      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2 rounded-full py-1 pl-2 pr-1 hover:bg-gray-100 dark:hover:bg-gray-800"
        >
          <span className="text-sm text-gray-600 dark:text-gray-300">{user.email}</span>
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 text-sm font-medium text-blue-700 dark:bg-blue-900 dark:text-blue-200">
            {initialOf(user.email)}
          </span>
        </button>

        {menuOpen && (
          <div className="absolute right-0 z-10 mt-2 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800">
            <button
              type="button"
              onClick={() => void logout()}
              className="block w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              ログアウトする
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
