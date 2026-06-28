"use client";

import { useState } from "react";
import Link from "next/link";
import {
  askAboutSelection,
  createFlashcard,
  generateFlashcard,
  grammarContext,
  translateSelection,
  type AskResponse,
  type CardSuggestion,
  type DictionaryEntry,
  type ReadingSegment,
  type TranslateResponse,
} from "@/lib/api";
import Markdown from "./Markdown";
import CardFields from "./CardFields";

// What the inspector acts on: a German word or phrase, the enclosing German
// sentence used as context, and that sentence's English (for flashcards).
interface Subject {
  text: string;
  context?: string | null;
  english?: string | null;
}

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

type Action = "translate" | "grammar" | "ask" | "card";

function Inspector({
  subject,
  bookId,
  onClose,
}: {
  subject: Subject;
  bookId: number;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState<Action | null>(null);
  const [translation, setTranslation] = useState<TranslateResponse | null>(null);
  const [grammar, setGrammar] = useState<AskResponse | null>(null);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const [draft, setDraft] = useState<CardSuggestion | null>(null);

  async function run<T>(action: Action, fn: () => Promise<T>, set: (v: T) => void) {
    setBusy(action);
    try {
      set(await fn());
    } catch {
      /* keep the inspector usable even if a lookup fails */
    } finally {
      setBusy(null);
    }
  }

  const doTranslate = () =>
    run("translate", () => translateSelection(subject.text, subject.context), setTranslation);
  const doGrammar = () =>
    run("grammar", () => grammarContext(subject.text, subject.context), setGrammar);
  const doAsk = () => {
    if (!question.trim()) return;
    return run("ask", () => askAboutSelection(subject.text, question, subject.context), setAnswer);
  };
  const doCard = () =>
    run(
      "card",
      () => generateFlashcard(subject.text, subject.context ?? subject.text, subject.english),
      setDraft,
    );

  return (
    <div className="inspector">
      <button className="inspector-close" onClick={onClose} title="Close">
        ✕
      </button>
      <p className="inspector-head">
        <strong>{subject.text.trim()}</strong>
      </p>

      <div className="inspector-actions">
        <button onClick={doTranslate} disabled={busy !== null}>
          {busy === "translate" ? "Translating…" : "Translate"}
        </button>
        <button onClick={doGrammar} disabled={busy !== null}>
          {busy === "grammar" ? "Looking up…" : "Grammar context"}
        </button>
        <button onClick={() => setAsking((v) => !v)} disabled={busy !== null}>
          Ask Claude
        </button>
        <button onClick={doCard} disabled={busy !== null}>
          {busy === "card" ? "Building…" : "Make flashcard"}
        </button>
      </div>

      {translation && (
        <div className="answer" style={{ marginTop: "0.8rem" }}>
          <p style={{ margin: 0 }}>{translation.translation}</p>
          {translation.note && (
            <p className="muted" style={{ margin: "0.5rem 0 0", fontSize: "0.9rem" }}>
              {translation.note}
            </p>
          )}
          {translation.dictionary && <DictionaryCard entry={translation.dictionary} />}
        </div>
      )}

      {grammar && (
        <div className="answer" style={{ marginTop: "0.8rem" }}>
          <Markdown>{grammar.answer}</Markdown>
          <SectionRefs numbers={grammar.section_numbers} />
        </div>
      )}

      {asking && (
        <form
          className="ask-row"
          onSubmit={(e) => {
            e.preventDefault();
            doAsk();
          }}
        >
          <input
            type="text"
            placeholder="Ask anything about this…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            autoFocus
          />
          <button type="submit" disabled={busy !== null || !question.trim()}>
            {busy === "ask" ? "Asking…" : "Ask"}
          </button>
        </form>
      )}

      {answer && (
        <div className="answer" style={{ marginTop: "0.8rem" }}>
          <Markdown>{answer.answer}</Markdown>
          <SectionRefs numbers={answer.section_numbers} />
        </div>
      )}

      {draft && (
        <CardDraftEditor
          draft={draft}
          setDraft={setDraft}
          bookId={bookId}
          onDismiss={() => setDraft(null)}
        />
      )}
    </div>
  );
}

// An editable preview of a generated flashcard. The learner can tweak any field
// before saving — highlights on the front/back follow the target words live.
function CardDraftEditor({
  draft,
  setDraft,
  bookId,
  onDismiss,
}: {
  draft: CardSuggestion;
  setDraft: (c: CardSuggestion) => void;
  bookId: number;
  onDismiss: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await createFlashcard({ ...draft, book_id: bookId });
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the card.");
    } finally {
      setSaving(false);
    }
  }

  if (saved) {
    return (
      <div className="card-draft">
        <p style={{ margin: 0 }}>
          ✓ Saved to your deck. <Link href="/flashcards">Review cards →</Link>
        </p>
        <button className="ghost" onClick={onDismiss} style={{ marginTop: "0.6rem" }}>
          Close
        </button>
      </div>
    );
  }

  return (
    <div className="card-draft">
      <p className="card-draft-label">New flashcard</p>

      <CardFields value={draft} onChange={setDraft} />

      {error && <p className="error" style={{ margin: "0.4rem 0 0" }}>{error}</p>}

      <div className="card-draft-actions">
        <button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save card"}
        </button>
        <button className="ghost" onClick={onDismiss} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function SectionRefs({ numbers }: { numbers: string[] }) {
  if (numbers.length === 0) return null;
  return (
    <p className="muted" style={{ marginBottom: 0 }}>
      Referenced:{" "}
      {numbers.map((num, i) => (
        <span key={num}>
          {i > 0 && ", "}
          <Link href={`/sections/${encodeURIComponent(num)}`}>§{num}</Link>
        </span>
      ))}
    </p>
  );
}

function DictionaryCard({ entry }: { entry: DictionaryEntry }) {
  return (
    <div className="dict-card">
      <p className="dict-head">
        {entry.gender && <span className="dict-gender">{entry.gender}</span>}{" "}
        <strong>{entry.word}</strong>
        <span className="muted">
          {" "}
          · {entry.part_of_speech}
          {entry.pronunciation ? ` · ${entry.pronunciation}` : ""}
        </span>
      </p>
      {entry.forms.length > 0 && (
        <ul className="dict-forms">
          {entry.forms.map((f) => (
            <li key={f.label}>
              <span className="muted">{f.label}:</span> {f.form}
            </li>
          ))}
        </ul>
      )}
      {entry.definitions.length > 0 && (
        <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.9rem" }}>
          {entry.definitions.join("; ")}
        </p>
      )}
      {entry.source_url && (
        <p className="dict-source muted">
          <a href={entry.source_url} target="_blank" rel="noreferrer">
            Wiktionary
          </a>
        </p>
      )}
    </div>
  );
}
