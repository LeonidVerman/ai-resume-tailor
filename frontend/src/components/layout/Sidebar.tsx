// frontend/src/components/layout/Sidebar.tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FileText,
  Briefcase,
  Zap,
  Clock,
  CreditCard,
  ShieldCheck,
  LogOut,
  Wand2,
  User,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/generate", label: "Generate", icon: Zap },
  { href: "/history", label: "History", icon: Clock },
  { href: "/billing", label: "Billing", icon: CreditCard },
];

const resourceItems = [
  { href: "/resumes", label: "Resumes", icon: FileText },
  { href: "/jobs", label: "Job Descriptions", icon: Briefcase },
  { href: "/profile", label: "Profile", icon: User },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(href + "/");

  return (
    <aside className="w-56 shrink-0 flex flex-col bg-gray-950 text-gray-300 min-h-screen">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-gray-800">
        <Link href="/dashboard" className="flex flex-col group">
          <div className="flex items-center gap-2">
            <Wand2 className="h-5 w-5 text-indigo-400 group-hover:text-indigo-300 transition-colors shrink-0" />
            <span className="font-semibold text-white text-sm tracking-tight">CVRocket</span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5 pl-7 leading-tight">AI resume tailoring for engineers</p>
        </Link>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
              isActive(href)
                ? "bg-indigo-500/25 text-indigo-200 font-medium"
                : "text-gray-500 hover:bg-white/[0.07] hover:text-gray-200"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </Link>
        ))}

        <div className="pt-4 pb-1 px-3">
          <p className="text-xs font-medium text-gray-600 uppercase tracking-wider">Resources</p>
        </div>

        {resourceItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
              isActive(href)
                ? "bg-indigo-500/25 text-indigo-200 font-medium"
                : "text-gray-500 hover:bg-white/[0.07] hover:text-gray-200"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </Link>
        ))}

        {user?.role === "admin" && (
          <>
            <div className="pt-4 pb-1 px-3">
              <p className="text-xs font-medium text-gray-600 uppercase tracking-wider">Admin</p>
            </div>
            <Link
              href="/admin"
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
                isActive("/admin")
                  ? "bg-indigo-600 text-white"
                  : "text-gray-400 hover:bg-gray-800 hover:text-white"
              )}
            >
              <ShieldCheck className="h-4 w-4 shrink-0" />
              Admin
            </Link>
          </>
        )}
      </nav>

      {/* User footer */}
      <div className="px-3 py-4 border-t border-gray-800">
        <div className="px-3 py-2 mb-1">
          <p className="text-xs text-gray-500 truncate">{user?.email ?? "—"}</p>
          <p className="text-xs font-medium text-gray-400 capitalize">{user?.plan_type ?? "free"} plan</p>
        </div>
        <button
          onClick={logout}
          className="flex items-center gap-3 px-3 py-2 w-full rounded-lg text-sm text-gray-500 hover:bg-white/[0.07] hover:text-gray-200 transition-colors"
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
