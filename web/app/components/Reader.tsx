"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { askClaude, type AskResponse, type BookDetail, type WeaveChunk } from "@/lib/api";
import Markdown from "./Markdown";

interface Selected {
  de: string;
  en: string;
  gloss: string | null;
}

export default function Reader({ book }: { book: BookDetail }) {
  const [density, setDensity] = useState(0.2);
  const [selected, setSelected] = useState<Selected | null>(null);

  // A word is woven into German once its frequency rank falls under the threshold.
  // rank 0 is the most frequent word, so low density still weaves the words you
  // meet most often — maximizing exposure.
  const threshold = Math.round(density * book.vocab_size);
  const isWoven = (c: WeaveChunk) => c.de != null && c.rank != null && c.rank < threshold;

  const wovenCount = useMemo(
    () =>
      book.segments.reduce(
        (n, s) => n + s.chunks.filter((c) => c.de != null && c.rank != null && c.rank < threshold).length,
        0,
      ),
    [book, threshold],
  );

  return (
    <>
      <div className="card density-card">
        <div className="density-row">
          <label htmlFor="density">German</label>
          <input
            id="density"
            type="range"
            min={0}
            max={100}
            value={Math.round(density * 100)}
            onChange={(e) => {
              setSelected(null);
              setDensity(Number(e.target.value) / 100);
            }}
          />
          <span className="density-pct">{Math.round(density * 100)}%</span>
        </div>
        <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.85rem" }}>
          {threshold} of {book.vocab_size} words active · {wovenCount} German words on the page
        </p>
      </div>

      <div className="reader-text">
        {book.segments.map((seg) => (
          <p key={seg.seq}>
            {seg.chunks.map((c, i) => {
              if (!isWoven(c)) return <span key={i}>{c.text}</span>;
              // The model may bake surrounding spaces into the chunk's English
              // text; keep them around the German so words don't run together.
              const lead = c.text.slice(0, c.text.length - c.text.trimStart().length);
              const trail = c.text.slice(c.text.trimEnd().length);
              return (
                <span key={i}>
                  {lead}
                  <span
                    className="woven"
                    onClick={() => setSelected({ de: c.de!, en: c.text, gloss: c.gloss })}
                  >
                    {c.de}
                  </span>
                  {trail}
                </span>
              );
            })}
          </p>
        ))}
      </div>

      {selected && <WordInspector word={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function WordInspector({ word, onClose }: { word: Selected; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [grammar, setGrammar] = useState<AskResponse | null>(null);

  async function explain() {
    setBusy(true);
    setGrammar(null);
    try {
      setGrammar(await askClaude(word.de));
    } catch {
      /* keep the inspector usable even if the lookup fails */
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inspector">
      <button className="inspector-close" onClick={onClose} title="Close">
        ✕
      </button>
      <p className="inspector-head">
        <strong>{word.de}</strong> <span className="muted">→ {word.en.trim()}</span>
      </p>
      {word.gloss && <p className="muted" style={{ margin: "0 0 0.6rem" }}>{word.gloss}</p>}
      <button onClick={explain} disabled={busy}>
        {busy ? "Looking up…" : "Explain the grammar"}
      </button>
      {grammar && (
        <div className="answer" style={{ marginTop: "0.8rem" }}>
          <Markdown>{grammar.answer}</Markdown>
          {grammar.section_numbers.length > 0 && (
            <p className="muted" style={{ marginBottom: 0 }}>
              Referenced:{" "}
              {grammar.section_numbers.map((num, i) => (
                <span key={num}>
                  {i > 0 && ", "}
                  <Link href={`/sections/${encodeURIComponent(num)}`}>§{num}</Link>
                </span>
              ))}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
