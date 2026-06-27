import { listBooks, type Book } from "@/lib/api";
import BookList from "../components/BookList";

export const dynamic = "force-dynamic";

export default async function ReadingPage() {
  let books: Book[] = [];
  let error: string | null = null;
  try {
    books = await listBooks();
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not load books.";
  }

  return (
    <>
      <h2>Reading</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Read a book as a <strong>diglot weave</strong>: the text starts in English and
        German words fade in as you raise the difficulty. Tap any German word to see what
        it means.
      </p>
      {error && <p className="error">{error}</p>}
      {!error && <BookList books={books} />}
    </>
  );
}
