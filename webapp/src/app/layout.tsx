import type { Metadata } from "next";
import { Josefin_Sans } from "next/font/google";
import "./globals.css";

const fontSans = Josefin_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "VidX",
  description: "Personalized video generator",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${fontSans.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
