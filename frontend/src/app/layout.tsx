import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CVRocket — AI Resume Tailoring for Software Engineers",
  description: "AI resume tailoring for software engineers. Generate targeted resumes and cover letters for each role.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased bg-slate-50 text-gray-900">{children}</body>
    </html>
  );
}
