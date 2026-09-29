import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { Shell } from "@/components/Shell";
import { SelectionProvider } from "@/lib/selection";
import "./globals.css";

const sans = IBM_Plex_Sans({ variable: "--font-plex-sans", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const mono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500"] });
const display = Archivo({ variable: "--font-archivo", subsets: ["latin"], weight: ["800", "900"] });

export const metadata: Metadata = {
  title: "Biokraft DCE",
  description: "Demand–Capacity Engine: uncertainty-aware allocation, spend and onboarding decisions.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${display.variable}`}>
      <body>
        <SelectionProvider>
          <Shell>{children}</Shell>
        </SelectionProvider>
      </body>
    </html>
  );
}
