"use client";

import { useEffect, useState } from "react";
import {
  checkFlashcardAnswer,
  deleteFlashcard,
  listDueFlashcards,
  listFlashcards,
  reviewFlashcard,
  updateFlashcard,
  type AnswerCheck,
  type Flashcard,
  type FlashcardData,
  type Rating,
} from "@/lib/api";
import { CardBack, highlight } from "./FlashcardFace";
import CardFields from "./CardFields";
import Markdown from "./Markdown";
import SectionRefs from "./SectionRefs";

type Tab = "review" | "manage";

export default function Flashcards() {
  const [tab, setTab] = useState<Tab>("review");

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <h2 style={{ margin: 0 }}>Flashcards</h2>
        <div className="toggle">
          <button className={`seg ${tab === "review" ? "active" : ""}`} onClick={() => setTab("review")}>
            Review
          </button>
          <button className={`seg ${tab === "manage" ? "active" : ""}`} onClick={() => setTab("manage")}>
            Manage
          </button>
        </div>
      </div>
      {tab === "review" ? <Review /> : <Manage />}
    </>
  );
}

// --- review: work the due queue one card at a time ---------------------------

function Review() {
  const [queue, setQueue] = useState<Flashcard[] | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [grading, setGrading] = useState(false);
  const [typed, setTyped] = useState("");
  const [review, setReview] = useState<AnswerCheck | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    listDueFlashcards().then(setQueue).catch(() => setQueue([]));
  }, []);

  if (queue === null) return <p className="muted">Loading…</p>;
  if (queue.length === 0) {
    return (
      <div className="card">
        <p style={{ margin: 0 }}>🎉 You&apos;re all caught up — no cards due right now.</p>
      </div>
    );
  }

  const card = queue[0];

  async function grade(rating: Rating) {
    setGrading(true);
    try {
      const updated = await reviewFlashcard(card.id, rating);
      setQueue((q) => {
        const rest = (q ?? []).slice(1);
        // 'again' keeps the card in this session until it's recalled.
        return rating === "again" ? [...rest, updated] : rest;
      });
      setRevealed(false);
      setTyped("");
      setReview(null);
    } finally {
      setGrading(false);
    }
  }

  // Ask Claude to assess what the learner typed, after the answer is shown.
  async function reviewWithClaude() {
    setChecking(true);
    try {
      setReview(await checkFlashcardAnswer(card.id, typed));
    } catch {
      /* leave the card usable if the check fails */
    } finally {
      setChecking(false);
    }
  }

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{queue.length} due</p>
      <div className="card review-card">
        <p className="card-sentence">{highlight(card.english, card.target_en)}</p>

        {revealed ? (
          <>
            {typed.trim() !== "" && (
              <p className="your-answer">
                {diffWords(typed, card.german).map((w, idx) => (
                  <span key={idx} className={w.ok ? "w-ok" : "w-bad"}>
                    {w.word}{" "}
                  </span>
                ))}
              </p>
            )}
            <hr className="card-rule" />
            <CardBack card={card} />

            {typed.trim() !== "" && !review && (
              <button
                className="ghost"
                onClick={reviewWithClaude}
                disabled={checking}
                style={{ marginTop: "1rem" }}
              >
                {checking ? "Reviewing…" : "Review with Claude"}
              </button>
            )}
            {review && (
              <div className={`claude-review ${review.correct ? "ok" : "bad"}`}>
                <span className="claude-verdict">{review.correct ? "✓ Correct" : "✗ Not quite"}</span>
                <Markdown>{review.feedback}</Markdown>
                {review.section_numbers.length > 0 && (
                  <p className="muted claude-refs">
                    Grammar: <SectionRefs refs={review.section_numbers} />
                  </p>
                )}
              </div>
            )}

            <div className="review-grades">
              <button className="grade again" onClick={() => grade("again")} disabled={grading}>
                Again
              </button>
              <button className="grade hard" onClick={() => grade("hard")} disabled={grading}>
                Hard
              </button>
              <button className="grade good" onClick={() => grade("good")} disabled={grading}>
                Good
              </button>
              <button className="grade easy" onClick={() => grade("easy")} disabled={grading}>
                Easy
              </button>
            </div>
          </>
        ) : (
          <>
            <textarea
              className="review-input"
              rows={2}
              value={typed}
              autoFocus
              placeholder="Write the German…"
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                // Enter reveals; Shift+Enter inserts a newline.
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  setRevealed(true);
                }
              }}
            />
            <button onClick={() => setRevealed(true)} style={{ marginTop: "0.8rem" }}>
              Show answer
            </button>
          </>
        )}
      </div>
    </>
  );
}

// Word-by-word check of the typed answer against the correct German. An LCS
// alignment marks which typed words appear, in order, in the answer — so a
// missing or extra word only colours itself red, not everything after it.
// Comparison ignores case and punctuation; the original typed word is shown.
function diffWords(typed: string, correct: string): { word: string; ok: boolean }[] {
  const norm = (w: string) => w.toLowerCase().replace(/[.,;:!?„“”"'»«…–—-]/g, "");
  const words = typed.trim().split(/\s+/).filter(Boolean);
  const a = words.map(norm);
  const b = correct.trim().split(/\s+/).filter(Boolean).map(norm);

  // LCS length table over the normalized token sequences.
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  // Backtrack: a typed word is correct if it's part of the longest common run.
  const matched = new Array(words.length).fill(false);
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      matched[i] = true;
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      j++;
    }
  }
  return words.map((word, k) => ({ word, ok: matched[k] }));
}

// --- manage: browse, edit and delete the deck --------------------------------

function Manage() {
  const [cards, setCards] = useState<Flashcard[] | null>(null);

  useEffect(() => {
    listFlashcards().then(setCards).catch(() => setCards([]));
  }, []);

  function replace(updated: Flashcard) {
    setCards((c) => (c ?? []).map((x) => (x.id === updated.id ? updated : x)));
  }

  async function remove(id: number) {
    await deleteFlashcard(id);
    setCards((c) => (c ?? []).filter((x) => x.id !== id));
  }

  if (cards === null) return <p className="muted">Loading…</p>;
  if (cards.length === 0) {
    return (
      <div className="card">
        <p style={{ margin: 0 }}>
          No cards yet. While reading, select a word and choose <strong>Make flashcard</strong>.
        </p>
      </div>
    );
  }

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{cards.length} cards</p>
      {cards.map((card) => (
        <ManageCard key={card.id} card={card} onSaved={replace} onDelete={() => remove(card.id)} />
      ))}
    </>
  );
}

// One card in the Manage list: a summary that expands to the full front/back,
// with an inline editor that saves content changes (the SM-2 schedule is kept).
function ManageCard({
  card,
  onSaved,
  onDelete,
}: {
  card: Flashcard;
  onSaved: (c: Flashcard) => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<FlashcardData>(card);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEditing() {
    setDraft(card);
    setError(null);
    setEditing(true);
    setExpanded(true);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(await updateFlashcard(card.id, draft));
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the card.");
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="card manage-card-open">
        <CardFields value={draft} onChange={(c) => setDraft({ ...c, book_id: draft.book_id })} />
        {error && <p className="error" style={{ margin: "0.4rem 0 0" }}>{error}</p>}
        <div className="card-draft-actions">
          <button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
          <button className="ghost" onClick={() => setEditing(false)} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card manage-card-open">
      <div className="manage-card">
        <button
          className="manage-card-body manage-card-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          <span>{highlight(card.english, card.target_en)}</span>
          <span className="muted" style={{ display: "block", margin: "0.2rem 0 0" }}>
            {highlight(card.german, card.target_de)}
          </span>
          <span className="muted manage-meta">due {card.due}</span>
        </button>
        <div className="manage-card-actions">
          <button className="ghost" onClick={startEditing} title="Edit card">
            Edit
          </button>
          <button className="ghost" onClick={onDelete} title="Delete card">
            Delete
          </button>
        </div>
      </div>
      {expanded && (
        <div style={{ marginTop: "0.8rem" }}>
          <hr className="card-rule" />
          <CardBack card={card} />
        </div>
      )}
    </div>
  );
}
