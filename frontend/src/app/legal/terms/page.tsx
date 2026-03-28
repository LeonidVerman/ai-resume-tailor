"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { legal } from "@/lib/api";

export default function TermsPage() {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    legal.documentContent("terms_of_service")
      .then(setContent)
      .catch(() => setError(true));
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 py-10 px-4">
      <div className="max-w-3xl mx-auto">
        <Link
          href="/legal/accept"
          className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline mb-6"
        >
          <ArrowLeft className="h-4 w-4" />
          Back
        </Link>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8">
          {content ? (
            <pre className="whitespace-pre-wrap font-sans text-sm text-gray-800 leading-relaxed">
              {content}
            </pre>
          ) : error ? (
            <p className="text-sm text-red-600">Could not load document. Please try again.</p>
          ) : (
            <div className="py-12 flex justify-center">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
