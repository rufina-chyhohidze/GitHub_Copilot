import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Repository Copilot — Read the code. See the evidence.",
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
