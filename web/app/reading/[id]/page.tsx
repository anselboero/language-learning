import Link from "next/link";
import { getBook, type BookDetail } from "@/lib/api";
import Reader from "../../components/Reader";

export const dynamic = "force-dynamic";

export default async function ReaderPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let book: BookDetail | null = null;
  let error: string | null = null;
  try {
    book = await getBook(Number(id));
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not load this book.";
  }

  return (
    <>
      <p style={{ marginTop: 0 }}>
        <Link href="/reading">← Reading</Link>
      </p>
      {error && <p className="error">{error}</p>}
      {book && (
        <>
          <h2 style={{ marginBottom: "0.2rem" }}>{book.title}</h2>
          {book.author && <p className="muted" style={{ marginTop: 0 }}>{book.author}</p>}
          <Reader book={book} />
        </>
      )}
    </>
  );
}
