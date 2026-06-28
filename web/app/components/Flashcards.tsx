"use client";

import { useEffect, useRef, useState } from "react";
import {
  checkDictation,
  checkFlashcardAnswer,
  clipMediaUrl,
  deleteFlashcard,
  listDueReview,
  listFlashcards,
  reviewFlashcard,
  reviewListeningClip,
  updateFlashcard,
  type AnswerCheck,
  type Flashcard,
  type FlashcardData,
  type ListeningClip,
  type Rating,
  type ReviewItem,
} from "@/lib/api";
import { CardBack, highlight } from "./FlashcardFace";
import CardFields from "./CardFields";
import Inspector, { type Subject } from "./Inspector";
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

// --- review: one mixed queue of vocab cards and listening clips --------------

type Filter = "all" | "vocab" | "listening";

const itemId = (i: ReviewItem) => (i.kind === "vocab" ? i.vocab!.id : i.listening!.id);

function Review() {
  const [queue, setQueue] = useState<ReviewItem[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    listDueReview().then(setQueue).catch(() => setQueue([]));
  }, []);

  // Drop the graded item from the queue; 'again' requeues it for later in the session.
  function afterGrade(item: ReviewItem, rating: Rating, updated: ReviewItem) {
    setQueue((q) => {
      const rest = (q ?? []).filter(
        (x) => !(x.kind === item.kind && itemId(x) === itemId(item)),
      );
      return rating === "again" ? [...rest, updated] : rest;
    });
  }

  if (queue === null) return <p className="muted">Loading…</p>;
  if (queue.length === 0) {
    return (
      <div className="card">
        <p style={{ margin: 0 }}>🎉 You&apos;re all caught up — nothing due right now.</p>
      </div>
    );
  }

  const vocabCount = queue.filter((i) => i.kind === "vocab").length;
  const listeningCount = queue.filter((i) => i.kind === "listening").length;
  const view = filter === "all" ? queue : queue.filter((i) => i.kind === filter);
  const current = view[0];

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginTop: 0 }}>
        <p className="muted" style={{ margin: 0 }}>{view.length} due</p>
        <div className="toggle">
          <button className={`seg ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>
            All ({queue.length})
          </button>
          <button className={`seg ${filter === "vocab" ? "active" : ""}`} onClick={() => setFilter("vocab")}>
            Vocab ({vocabCount})
          </button>
          <button
            className={`seg ${filter === "listening" ? "active" : ""}`}
            onClick={() => setFilter("listening")}
          >
            Listening ({listeningCount})
          </button>
        </div>
      </div>

      {!current ? (
        <div className="card">
          <p style={{ margin: 0 }}>Nothing due in this filter.</p>
        </div>
      ) : current.kind === "vocab" ? (
        <VocabReviewCard
          key={`v${current.vocab!.id}`}
          card={current.vocab!}
          onGraded={(rating, updated) =>
            afterGrade(current, rating, { kind: "vocab", due: updated.due, vocab: updated, listening: null })
          }
        />
      ) : (
        <ListeningReviewCard
          key={`l${current.listening!.id}`}
          clip={current.listening!}
          onGraded={(rating, updated) =>
            afterGrade(current, rating, {
              kind: "listening",
              due: updated.due,
              vocab: null,
              listening: updated,
            })
          }
        />
      )}
    </>
  );
}

// The four-button SM-2 grade row, shared by both card kinds.
function GradeRow({ onGrade, disabled }: { onGrade: (r: Rating) => void; disabled: boolean }) {
  return (
    <div className="review-grades">
      <button className="grade again" onClick={() => onGrade("again")} disabled={disabled}>
        Again
      </button>
      <button className="grade hard" onClick={() => onGrade("hard")} disabled={disabled}>
        Hard
      </button>
      <button className="grade good" onClick={() => onGrade("good")} disabled={disabled}>
        Good
      </button>
      <button className="grade easy" onClick={() => onGrade("easy")} disabled={disabled}>
        Easy
      </button>
    </div>
  );
}

// Claude's verdict block, shared by both card kinds.
function ClaudeReview({ review }: { review: AnswerCheck }) {
  return (
    <div className={`claude-review ${review.correct ? "ok" : "bad"}`}>
      <span className="claude-verdict">{review.correct ? "✓ Correct" : "✗ Not quite"}</span>
      <Markdown>{review.feedback}</Markdown>
      {review.section_numbers.length > 0 && (
        <p className="muted claude-refs">
          Grammar: <SectionRefs refs={review.section_numbers} />
        </p>
      )}
    </div>
  );
}

// A vocab card: shown the English, recall the German, reveal the back, grade.
function VocabReviewCard({
  card,
  onGraded,
}: {
  card: Flashcard;
  onGraded: (rating: Rating, updated: Flashcard) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const [grading, setGrading] = useState(false);
  const [typed, setTyped] = useState("");
  const [review, setReview] = useState<AnswerCheck | null>(null);
  const [checking, setChecking] = useState(false);

  async function grade(rating: Rating) {
    setGrading(true);
    try {
      onGraded(rating, await reviewFlashcard(card.id, rating));
    } finally {
      setGrading(false);
    }
  }

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
            <button className="ghost" onClick={reviewWithClaude} disabled={checking} style={{ marginTop: "1rem" }}>
              {checking ? "Reviewing…" : "Review with Claude"}
            </button>
          )}
          {review && <ClaudeReview review={review} />}

          <GradeRow onGrade={grade} disabled={grading} />
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
  );
}

// A listening clip: hear it, transcribe the German by ear, reveal the transcript
// (with the diff), grade. The revealed transcript is selectable for lookup, and
// the English stays a tap away.
const SPEEDS = [0.75, 1, 1.25];

function ListeningReviewCard({
  clip,
  onGraded,
}: {
  clip: ListeningClip;
  onGraded: (rating: Rating, updated: ListeningClip) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [speed, setSpeed] = useState(1);
  const [revealed, setRevealed] = useState(false);
  const [showEnglish, setShowEnglish] = useState(false);
  const [grading, setGrading] = useState(false);
  const [typed, setTyped] = useState("");
  const [review, setReview] = useState<AnswerCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [subject, setSubject] = useState<Subject | null>(null);

  // Play only the clip's span: seek to its start, then pause once it reaches the end.
  function playClip() {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = clip.start_ms / 1000;
    v.playbackRate = speed;
    void v.play();
  }

  function onTimeUpdate() {
    const v = videoRef.current;
    if (v && v.currentTime >= clip.end_ms / 1000) v.pause();
  }

  function setRate(rate: number) {
    setSpeed(rate);
    if (videoRef.current) videoRef.current.playbackRate = rate;
  }

  async function grade(rating: Rating) {
    setGrading(true);
    try {
      onGraded(rating, await reviewListeningClip(clip.id, rating));
    } finally {
      setGrading(false);
    }
  }

  async function reviewWithClaude() {
    setChecking(true);
    try {
      setReview(await checkDictation(clip.id, typed));
    } catch {
      /* leave the clip usable if the check fails */
    } finally {
      setChecking(false);
    }
  }

  // Open the inspector on whatever German the learner highlighted in the transcript.
  function captureSelection() {
    const text = window.getSelection()?.toString().trim();
    if (text) setSubject({ text, context: clip.transcript_de, english: clip.transcript_en });
  }

  return (
    <div className="card review-card">
      <p className="muted listening-meta" style={{ marginTop: 0 }}>
        🎧 Listening · {clip.difficulty} · {clip.topic}
      </p>

      <video
        ref={videoRef}
        src={clipMediaUrl(clip.source_id)}
        className="listening-video"
        onTimeUpdate={onTimeUpdate}
        preload="metadata"
        playsInline
      />

      <div className="listening-controls">
        <button onClick={playClip}>▶ Play clip</button>
        <div className="toggle listening-speeds">
          {SPEEDS.map((s) => (
            <button key={s} className={`seg ${speed === s ? "active" : ""}`} onClick={() => setRate(s)}>
              {s}×
            </button>
          ))}
        </div>
      </div>

      {revealed ? (
        <>
          {typed.trim() !== "" && (
            <p className="your-answer">
              {diffWords(typed, clip.transcript_de).map((w, idx) => (
                <span key={idx} className={w.ok ? "w-ok" : "w-bad"}>
                  {w.word}{" "}
                </span>
              ))}
            </p>
          )}
          <hr className="card-rule" />
          <p className="card-sentence" onMouseUp={captureSelection} title="Select any phrase to look it up">
            {clip.transcript_de}
          </p>

          {showEnglish ? (
            <p className="muted" style={{ marginTop: "0.4rem" }}>{clip.transcript_en}</p>
          ) : (
            <button className="ghost" onClick={() => setShowEnglish(true)} style={{ marginTop: "0.4rem" }}>
              Show English
            </button>
          )}

          {typed.trim() !== "" && !review && (
            <button className="ghost" onClick={reviewWithClaude} disabled={checking} style={{ marginTop: "1rem" }}>
              {checking ? "Reviewing…" : "Review with Claude"}
            </button>
          )}
          {review && <ClaudeReview review={review} />}

          <GradeRow onGrade={grade} disabled={grading} />

          {subject && (
            <Inspector
              key={`${subject.text}|${subject.context ?? ""}`}
              subject={subject}
              bookId={null}
              onClose={() => setSubject(null)}
            />
          )}
        </>
      ) : (
        <>
          <textarea
            className="review-input"
            rows={2}
            value={typed}
            placeholder="Type the German you hear…"
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                setRevealed(true);
              }
            }}
          />
          <button onClick={() => setRevealed(true)} style={{ marginTop: "0.8rem" }}>
            Show transcript
          </button>
        </>
      )}
    </div>
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
