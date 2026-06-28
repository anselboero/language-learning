import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Language Learning",
  description: "Science-based, gamified language learning — grammar, powered by Claude.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site">
          <div className="inner">
            <h1>
              <Link href="/" style={{ color: "inherit", textDecoration: "none" }}>
                📚 Language Learning
              </Link>
            </h1>
            <nav>
              <Link href="/sections">Grammar</Link>
              <Link href="/reading">Reading</Link>
              <Link href="/flashcards">Cards</Link>
              <Link href="/upload" className="upload-link">
                Upload
              </Link>
            </nav>
          </div>
        </header>
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
