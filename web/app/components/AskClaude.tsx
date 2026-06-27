"use client";

import { useState } from "react";
import Link from "next/link";
import { askClaude, type AskResponse } from "@/lib/api";
import Markdown from "./Markdown";

export default function AskClaude() {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleAsk() {
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await askClaude(q));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lookup failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Ask Claude</h2>
      <p className="muted">
        Type a word, phrase, or grammar question. Claude finds the relevant rule in
        your ingested grammar and explains it.
      </p>
      <textarea
        rows={2}
        value={query}
        placeholder='e.g. "wäre" or "when do I use Konjunktiv II?"'
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAsk();
        }}
      />
      <div style={{ marginTop: "0.6rem" }}>
        <button onClick={handleAsk} disabled={busy || !query.trim()}>
          {busy ? "Thinking…" : "Ask"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="answer">
          <Markdown>{result.answer}</Markdown>
          {result.section_numbers.length > 0 && (
            <p className="muted" style={{ marginBottom: 0 }}>
              Referenced:{" "}
              {result.section_numbers.map((num, i) => (
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
