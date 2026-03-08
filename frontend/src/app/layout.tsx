import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Resume Tailor",
  description: "Generate tailored resumes and cover letters for any job",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
