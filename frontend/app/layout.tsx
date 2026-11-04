import type { Metadata } from "next";
import { JetBrains_Mono, Nunito } from "next/font/google";

import "./globals.css";

import { THEME_SCRIPT } from "@/lib/theme";

const nunito = Nunito({
  variable: "--font-nunito",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Bleep",
  description: "On-call agent for Docker hosts.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // The inline script sets the `dark` class before React hydrates, which React would otherwise
    // report as a mismatch.
    <html
      lang="en"
      className={`${nunito.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}
