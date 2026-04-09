import Link from "next/link";

export function PublicFooter() {
  return (
    <footer className="py-6 px-4 text-center">
      <p className="text-sm font-medium text-gray-700">CVRocket</p>
      <p className="text-xs text-gray-400 mt-0.5">AI resume tailoring for software engineers</p>
      <p className="mt-2 text-xs text-gray-500">
        Questions or support?{" "}
        <a
          href="mailto:support@cvrocket.io"
          className="text-indigo-600 hover:underline"
        >
          support@cvrocket.io
        </a>
      </p>
      <p className="mt-2 flex items-center justify-center gap-3 text-xs text-gray-400">
        <Link href="/legal/terms" className="hover:text-gray-600 hover:underline">
          Terms of Service
        </Link>
        <span aria-hidden="true">·</span>
        <Link href="/legal/privacy" className="hover:text-gray-600 hover:underline">
          Privacy Notice
        </Link>
      </p>
    </footer>
  );
}
