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
        Read a book in German and reveal the English line by line. Select any word or
        phrase to translate it, see its grammar, or make a flashcard.
      </p>
      {error && <p className="error">{error}</p>}
      {!error && <BookList books={books} />}
    </>
  );
}
