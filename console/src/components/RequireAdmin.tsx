import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/useAuth";

/**
 * adminロール以外なら/へリダイレクトする。RequireAuthの内側で使うこと
 * (未認証の場合の扱いはRequireAuthに任せるため、ここではuserが存在する
 * 前提でroleだけ見る)。実データはgateway側のrequire_adminで既に保護されて
 * いるが、admin専用ページのURLを一般ユーザーが直接開いたときに空のplaceholder
 * が見えてしまうのを避けるための表示上のガード。
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
