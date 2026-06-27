"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ingestBook } from "@/lib/api";

export default function ReadingUpload() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [english, setEnglish] = useState<File | null>(null);
  const [german, setGerman] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ready = title.trim() && english && german;

  async function handleUpload() {
    if (!ready) return;
    setBusy(true);
    setStatus(null);
    setError(null);
    try {
      const book = await ingestBook(title.trim(), author.trim(), english!, german!);
      setStatus(`Done — "${book.title}" aligned into ${book.segment_count} segments (${book.vocab_size} weaveable words).`);
      setTitle("");
      setAuthor("");
      setEnglish(null);
      setGerman(null);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Add a book</h2>
      <p className="muted">
        Upload the same book as two plain-text files — the English version and its German
        translation. Claude aligns them sentence by sentence and marks the weaveable
        vocabulary.
      </p>
      <input
        type="text"
        placeholder="Title — e.g. Little Red Riding Hood"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        style={{ marginBottom: "0.6rem" }}
      />
      <input
        type="text"
        placeholder="Author (optional)"
        value={author}
        onChange={(e) => setAuthor(e.target.value)}
        style={{ marginBottom: "0.8rem" }}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        <label className="muted" style={{ fontSize: "0.9rem" }}>
          English text (.txt)
          <input
            type="file"
            accept=".txt,text/plain"
            onChange={(e) => setEnglish(e.target.files?.[0] ?? null)}
            style={{ display: "block", marginTop: "0.25rem" }}
          />
        </label>
        <label className="muted" style={{ fontSize: "0.9rem" }}>
          German text (.txt)
          <input
            type="file"
            accept=".txt,text/plain"
            onChange={(e) => setGerman(e.target.files?.[0] ?? null)}
            style={{ display: "block", marginTop: "0.25rem" }}
          />
        </label>
      </div>
      <div style={{ marginTop: "0.9rem" }}>
        <button onClick={handleUpload} disabled={!ready || busy}>
          {busy ? "Aligning…" : "Align & add"}
        </button>
      </div>
      {busy && (
        <p className="muted" style={{ marginBottom: 0 }}>
          Claude is aligning the two texts — this can take a minute.
        </p>
      )}
      {status && <p style={{ marginBottom: 0 }}>{status}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
