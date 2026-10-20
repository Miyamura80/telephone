import "./global.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Telephone Documentation",
  description: "An MCP switchboard that lets two AI agents hold a two-way conversation with no human in the loop",
  icons: {
    icon: [
      {
        url: "/favicon.ico",
      },
      {
        url: "/icon-light.png",
        media: "(prefers-color-scheme: light)",
      },
      {
        url: "/icon-dark.png",
        media: "(prefers-color-scheme: dark)",
      },
    ],
    apple: "/icon-light.png",
  },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
