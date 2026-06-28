import Link from "next/link";
import { getBook, getChapter, type BookDetail } from "@/lib/api";
import Reader from "../../components/Reader";

export const dynamic = "force-dynamic";

export default async function BookPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const bookId = Number(id);
  let book: BookDetail | null = null;
  let error: string | null = null;
  try {
    book = await getBook(bookId);
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not load this book.";
  }

  // A book with no real chapter divisions reads straight through, as before.
  if (book && book.chapter_count <= 1) {
    const chapter = await getChapter(bookId, 0);
    return (
      <>
        <BackLink />
        <h2 style={{ marginBottom: "0.2rem" }}>{book.title}</h2>
        {book.author && <p className="muted" style={{ marginTop: 0 }}>{book.author}</p>}
        <Reader bookId={bookId} segments={chapter.segments} />
      </>
    );
  }

  return (
    <>
      <BackLink />
      {error && <p className="error">{error}</p>}
      {book && (
        <>
          <h2 style={{ marginBottom: "0.2rem" }}>{book.title}</h2>
          {book.author && <p className="muted" style={{ marginTop: 0 }}>{book.author}</p>}
          <p className="muted" style={{ marginTop: 0 }}>
            {book.chapter_count} chapters · {book.segment_count} segments
          </p>
          <div style={{ marginTop: "1rem" }}>
            {book.chapters.map((ch) => (
              <Link
                key={ch.idx}
                href={`/reading/${bookId}/${ch.idx}`}
                className="section-link"
              >
                <strong>{ch.title}</strong>
                <span className="muted">{ch.segment_count} segments</span>
              </Link>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function BackLink() {
  return (
    <p style={{ marginTop: 0 }}>
      <Link href="/reading">← Reading</Link>
    </p>
  );
}
