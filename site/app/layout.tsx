import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Nunito } from "next/font/google";

import "./globals.css";

const nunito = Nunito({
  variable: "--font-nunito",
  subsets: ["latin"],
  weight: ["400", "600", "700", "800", "900"],
});
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400"],
});

const description =
  "Bleep watches the containers on your Docker hosts, finds out why one broke, and proposes a fix. Nothing changes until you say yes.";

export const metadata: Metadata = {
  title: "Bleep: the on-call bot that asks before it fixes",
  description,
  openGraph: { title: "Bleep", description, type: "website" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f3fa" },
    { media: "(prefers-color-scheme: dark)", color: "#14132a" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${nunito.variable} ${jetbrainsMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
