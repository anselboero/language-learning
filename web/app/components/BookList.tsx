"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { deleteBook, type Book } from "@/lib/api";

export default function BookList({ books }: { books: Book[] }) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<number | null>(null);

  async function handleDelete(id: number, title: string) {
    if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
    setBusyId(id);
    try {
      await deleteBook(id);
      router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  function readRandom() {
    const pick = books[Math.floor(Math.random() * books.length)];
    if (pick) router.push(`/reading/${pick.id}`);
  }

  if (books.length === 0) {
    return <p className="muted">No books yet. Add one below to start reading.</p>;
  }

  return (
    <>
      <div style={{ marginBottom: "1rem" }}>
        <button onClick={readRandom}>🎲 Read a random book</button>
      </div>
      {books.map((book) => (
        <div key={book.id} className="section-row">
          <Link href={`/reading/${book.id}`} className="section-link">
            <strong>{book.title}</strong>
            <span className="muted">
              {book.author ? `${book.author} · ` : ""}
              {book.segment_count} segments · {book.vocab_size} weaveable words
            </span>
          </Link>
          <button
            className="chevron-btn"
            title="Delete book"
            disabled={busyId === book.id}
            onClick={() => handleDelete(book.id, book.title)}
          >
            ✕
          </button>
        </div>
      ))}
    </>
  );
}
