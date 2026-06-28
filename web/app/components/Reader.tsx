"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  askAboutSelection,
  createFlashcard,
  generateFlashcard,
  grammarContext,
  translateSelection,
  type AskResponse,
  type BookDetail,
  type CardSuggestion,
  type DictionaryEntry,
  type TranslateResponse,
  type WeaveChunk,
} from "@/lib/api";
import Markdown from "./Markdown";

// What the inspector acts on: a German word or phrase, plus optional surface
// info (filled when it came from tapping a woven word), the enclosing German
// sentence used as context, and that sentence's English (for flashcards).
interface Subject {
  text: string;
  en?: string | null;
  gloss?: string | null;
  context?: string | null;
  english?: string | null;
}

export default function Reader({ book }: { book: BookDetail }) {
  const [density, setDensity] = useState(0.2);
  const [subject, setSubject] = useState<Subject | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());

  // At the top of the slider, weaving content words still leaves an English
  // skeleton (function words never become German), so 100% shows the real,
  // verbatim German prose instead — tap a line to reveal its English.
  const fullGerman = density >= 1;

  // A word is woven into German once its frequency rank falls under the threshold.
  // rank 0 is the most frequent word, so low density still weaves the words you
  // meet most often — maximizing exposure.
  const threshold = Math.round(density * book.vocab_size);
  const isWoven = (c: WeaveChunk) => c.de != null && c.rank != null && c.rank < threshold;

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
              setSubject(null);
              setDensity(Number(e.target.value) / 100);
            }}
          />
          <span className="density-pct">{Math.round(density * 100)}%</span>
        </div>
        <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.85rem" }}>
          {fullGerman
            ? "Full German text · select any phrase to look it up · tap a line to reveal the English"
            : `${threshold} of ${book.vocab_size} words active · ${wovenCount} German words on the page · select German to look it up`}
        </p>
      </div>

      <div className="reader-text">
        {book.segments.map((seg) =>
          fullGerman ? (
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
          ) : (
            <p key={seg.seq} onMouseUp={() => captureSelection(seg.german, seg.english)}>
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
                      onClick={() => {
                        // A click is a collapsed selection; inspect the word itself.
                        if (window.getSelection()?.toString().trim()) return;
                        setSubject({ text: c.de!, en: c.text, gloss: c.gloss, context: seg.german, english: seg.english });
                      }}
                    >
                      {c.de}
                    </span>
                    {trail}
                  </span>
                );
              })}
            </p>
          ),
        )}
      </div>

      {subject && (
        <Inspector
          key={`${subject.text}|${subject.en ?? ""}`}
          subject={subject}
          bookId={book.id}
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
        {subject.en && <span className="muted"> → {subject.en.trim()}</span>}
      </p>
      {subject.gloss && <p className="muted" style={{ margin: "0 0 0.6rem" }}>{subject.gloss}</p>}

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

  const set = <K extends keyof CardSuggestion>(key: K, value: CardSuggestion[K]) =>
    setDraft({ ...draft, [key]: value });
  const setDecl = (key: keyof CardSuggestion["declension"], value: string) =>
    setDraft({ ...draft, declension: { ...draft.declension, [key]: value || null } });

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

      <label className="card-field">
        <span>Front (English)</span>
        <input type="text" value={draft.english} onChange={(e) => set("english", e.target.value)} />
      </label>
      <label className="card-field">
        <span>Back (German)</span>
        <input type="text" value={draft.german} onChange={(e) => set("german", e.target.value)} />
      </label>
      <div className="card-field-row">
        <label className="card-field">
          <span>Highlight (EN)</span>
          <input type="text" value={draft.target_en} onChange={(e) => set("target_en", e.target.value)} />
        </label>
        <label className="card-field">
          <span>Highlight (DE)</span>
          <input type="text" value={draft.target_de} onChange={(e) => set("target_de", e.target.value)} />
        </label>
      </div>

      <div className="card-field-row">
        <label className="card-field" style={{ flex: "0 0 7rem" }}>
          <span>Type</span>
          <select value={draft.pos} onChange={(e) => set("pos", e.target.value)}>
            <option value="noun">noun</option>
            <option value="verb">verb</option>
            <option value="other">other</option>
          </select>
        </label>
        <label className="card-field">
          <span>Lemma</span>
          <input type="text" value={draft.lemma} onChange={(e) => set("lemma", e.target.value)} />
        </label>
      </div>

      {draft.pos === "noun" && (
        <div className="card-field-row">
          <label className="card-field" style={{ flex: "0 0 7rem" }}>
            <span>Gender</span>
            <select value={draft.declension.gender ?? ""} onChange={(e) => setDecl("gender", e.target.value)}>
              <option value="">—</option>
              <option value="der">der</option>
              <option value="die">die</option>
              <option value="das">das</option>
            </select>
          </label>
          <label className="card-field">
            <span>Plural</span>
            <input
              type="text"
              value={draft.declension.plural ?? ""}
              onChange={(e) => setDecl("plural", e.target.value)}
            />
          </label>
        </div>
      )}

      {draft.pos === "verb" && (
        <div className="card-field-row">
          <label className="card-field">
            <span>Infinitive</span>
            <input
              type="text"
              value={draft.declension.infinitive ?? ""}
              onChange={(e) => setDecl("infinitive", e.target.value)}
            />
          </label>
          <label className="card-field">
            <span>Präteritum</span>
            <input
              type="text"
              value={draft.declension.preterite ?? ""}
              onChange={(e) => setDecl("preterite", e.target.value)}
            />
          </label>
          <label className="card-field">
            <span>Perfekt</span>
            <input
              type="text"
              value={draft.declension.perfect ?? ""}
              onChange={(e) => setDecl("perfect", e.target.value)}
            />
          </label>
        </div>
      )}

      <label className="card-field">
        <span>Context Note (optional)</span>
        <textarea
          rows={3}
          value={draft.note ?? ""}
          onChange={(e) => set("note", e.target.value || null)}
          placeholder="A short grammar note, if relevant"
        />
      </label>

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
