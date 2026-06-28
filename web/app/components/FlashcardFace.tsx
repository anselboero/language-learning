import type { ReactNode } from "react";
import type { CardDeclension, CardSuggestion } from "@/lib/api";

// Wrap the first case-insensitive occurrence of `target` in `text` with a
// highlight, leaving the rest verbatim. Falls back to plain text if the target
// isn't found (e.g. after the learner edited the sentence).
export function highlight(text: string, target: string): ReactNode {
  const t = target.trim();
  if (!t) return text;
  const at = text.toLowerCase().indexOf(t.toLowerCase());
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <mark className="card-hl">{text.slice(at, at + t.length)}</mark>
      {text.slice(at + t.length)}
    </>
  );
}

// The one-line inflection prompt shown on the back: nouns as "das Gemüse (—)",
// verbs as the three principal parts. Returns null when there's nothing to show.
export function declensionLine(pos: string, lemma: string, d: CardDeclension): string | null {
  if (pos === "noun") {
    const plural = d.plural?.trim() || "—";
    return `${lemma} (${plural})`;
  }
  if (pos === "verb") {
    const parts = [d.infinitive || lemma, d.preterite, d.perfect].filter(Boolean);
    return parts.join(" – ");
  }
  return null;
}

// The card back: German sentence (target highlighted), the declension line, and
// an optional grammar Context Note. Shared by the review screen and the editor.
export function CardBack({ card }: { card: Pick<CardSuggestion, "german" | "target_de" | "pos" | "lemma" | "declension" | "note"> }) {
  const line = declensionLine(card.pos, card.lemma, card.declension);
  return (
    <div className="card-back">
      <p className="card-sentence">{highlight(card.german, card.target_de)}</p>
      {line && <p className="card-declension">{line}</p>}
      {card.note && (
        <p className="card-note">
          <strong>Context Note:</strong> {card.note}
        </p>
      )}
    </div>
  );
}
