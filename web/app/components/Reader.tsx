"use client";

import { useState } from "react";
import { type ReadingSegment } from "@/lib/api";
import Inspector, { type Subject } from "./Inspector";

export default function Reader({
  bookId,
  segments,
}: {
  bookId: number;
  segments: ReadingSegment[];
}) {
  const [subject, setSubject] = useState<Subject | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());

  const toggleReveal = (seq: number) =>
    setRevealed((prev) => {
      const next = new Set(prev);
      next.has(seq) ? next.delete(seq) : next.add(seq);
      return next;
    });

  // Open the inspector on whatever German the reader highlighted, using the
  // segment's German sentence as context. Returns whether a selection was found,
  // so callers can suppress a competing click (e.g. the reveal toggle).
  const captureSelection = (context: string, english: string): boolean => {
    const text = window.getSelection()?.toString().trim();
    if (!text) return false;
    setSubject({ text, context, english });
    return true;
  };

  return (
    <>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Select any phrase to look it up · tap a line to reveal the English
      </p>

      <div className="reader-text">
        {segments.map((seg) => (
          <p
            key={seg.seq}
            className="full-de"
            onMouseUp={() => captureSelection(seg.german, seg.english)}
            onClick={() => {
              // A drag to select text also fires click; don't toggle then.
              if (window.getSelection()?.toString().trim()) return;
              toggleReveal(seg.seq);
            }}
            title="Tap to reveal the English · select text to look it up"
          >
            {seg.german}
            {revealed.has(seg.seq) && (
              <span className="reveal-en"> — {seg.english}</span>
            )}
          </p>
        ))}
      </div>

      {subject && (
        <Inspector
          key={`${subject.text}|${subject.context ?? ""}`}
          subject={subject}
          bookId={bookId}
          onClose={() => setSubject(null)}
        />
      )}
    </>
  );
}
