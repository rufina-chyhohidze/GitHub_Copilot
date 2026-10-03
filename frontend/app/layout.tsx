import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "GitHub Copilot — Don’t judge a repo by its cover. Look inside.",
  description:
    "An evidence-backed workspace for understanding public GitHub repositories.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
