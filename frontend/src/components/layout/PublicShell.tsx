// frontend/src/components/layout/PublicShell.tsx
//
// Public page shell for unauthenticated surfaces (e.g. /try).
// Unlike AppShell it performs NO auth redirect — anonymous visitors are the
// intended audience. Logo header + centered content + the shared PublicFooter
// (which carries the Terms of Service / Privacy Notice links).

import Link from "next/link";
import { Wand2 } from "lucide-react";
import { PublicFooter } from "./PublicFooter";

interface PublicShellProps {
  children: React.ReactNode;
}

export function PublicShell({ children }: PublicShellProps) {
  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      <header className="px-6 py-4">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link href="/try" className="inline-flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-600">
              <Wand2 className="h-5 w-5 text-white" />
            </span>
            <span className="text-lg font-bold text-gray-900">CVRocket</span>
          </Link>
          <Link
            href="/login"
            className="text-sm font-medium text-indigo-600 hover:text-indigo-500"
          >
            Sign in
          </Link>
        </div>
      </header>
      <main className="flex-1 px-4">
        <div className="max-w-3xl mx-auto py-8">{children}</div>
      </main>
      <PublicFooter />
    </div>
  );
}
