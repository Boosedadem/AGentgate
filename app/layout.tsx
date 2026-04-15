import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentGate",
  description:
    "Local runtime control plane for agent tool calls. FastAPI policy API, Python SDK, demo agent, and operational dashboard.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
