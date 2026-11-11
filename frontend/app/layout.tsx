import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Nunito } from "next/font/google";

import "./globals.css";

import { LANGUAGE_SCRIPT } from "@/lib/i18n/script";
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

// Lets the page draw under the iPhone's home indicator and notch, so env(safe-area-inset-*) has
// real values; the body and the phone's pinned decision bar pad by them.
export const viewport: Viewport = { viewportFit: "cover" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // The inline scripts set the `dark` class and `lang` before React hydrates, which React would
    // otherwise report as a mismatch.
    <html
      lang="en"
      className={`${nunito.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <script dangerouslySetInnerHTML={{ __html: LANGUAGE_SCRIPT }} />
      </head>
      <body className="min-h-dvh pr-[env(safe-area-inset-right)] pl-[env(safe-area-inset-left)]">
        {children}
      </body>
    </html>
  );
}
