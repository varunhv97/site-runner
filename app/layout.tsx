import type { Metadata } from "next";
import { DM_Mono, Figtree, Gabarito } from "next/font/google";
import "./globals.css";

// Same pairing as the landing page (dealt by taste-skill): Gabarito display, Figtree text, DM Mono.
const display = Gabarito({ variable: "--font-display", subsets: ["latin"] });
const text = Figtree({ variable: "--font-text", subsets: ["latin"] });
const mono = DM_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "500"] });

export const metadata: Metadata = {
  title: "Site Runner",
  icons: { icon: "/favicon.svg" },
  description: "Run HTML sites in your browser, version them, share a link.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${text.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
