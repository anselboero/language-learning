"use client";

import type { CardSuggestion } from "@/lib/api";
import Markdown from "./Markdown";
import SectionRefs from "./SectionRefs";

// The editable fields of a flashcard, shared by the reader's "New flashcard"
// draft and the Manage tab's inline editor. Front/back highlights follow the
// target words live; the Context Note shows a rendered Markdown preview.
export default function CardFields({
  value,
  onChange,
}: {
  value: CardSuggestion;
  onChange: (c: CardSuggestion) => void;
}) {
  const set = <K extends keyof CardSuggestion>(key: K, v: CardSuggestion[K]) =>
    onChange({ ...value, [key]: v });
  const setDecl = (key: keyof CardSuggestion["declension"], v: string) =>
    onChange({ ...value, declension: { ...value.declension, [key]: v || null } });

  return (
    <>
      <label className="card-field">
        <span>Front (English)</span>
        <input type="text" value={value.english} onChange={(e) => set("english", e.target.value)} />
      </label>
      <label className="card-field">
        <span>Back (German)</span>
        <input type="text" value={value.german} onChange={(e) => set("german", e.target.value)} />
      </label>
      <div className="card-field-row">
        <label className="card-field">
          <span>Highlight (EN)</span>
          <input type="text" value={value.target_en} onChange={(e) => set("target_en", e.target.value)} />
        </label>
        <label className="card-field">
          <span>Highlight (DE)</span>
          <input type="text" value={value.target_de} onChange={(e) => set("target_de", e.target.value)} />
        </label>
      </div>

      <div className="card-field-row">
        <label className="card-field" style={{ flex: "0 0 7rem" }}>
          <span>Type</span>
          <select value={value.pos} onChange={(e) => set("pos", e.target.value)}>
            <option value="noun">noun</option>
            <option value="verb">verb</option>
            <option value="other">other</option>
          </select>
        </label>
        <label className="card-field">
          <span>Lemma</span>
          <input type="text" value={value.lemma} onChange={(e) => set("lemma", e.target.value)} />
        </label>
      </div>

      {value.pos === "noun" && (
        <div className="card-field-row">
          <label className="card-field" style={{ flex: "0 0 7rem" }}>
            <span>Gender</span>
            <select value={value.declension.gender ?? ""} onChange={(e) => setDecl("gender", e.target.value)}>
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
              value={value.declension.plural ?? ""}
              onChange={(e) => setDecl("plural", e.target.value)}
            />
          </label>
        </div>
      )}

      {value.pos === "verb" && (
        <div className="card-field-row">
          <label className="card-field">
            <span>Infinitive</span>
            <input
              type="text"
              value={value.declension.infinitive ?? ""}
              onChange={(e) => setDecl("infinitive", e.target.value)}
            />
          </label>
          <label className="card-field">
            <span>Präteritum</span>
            <input
              type="text"
              value={value.declension.preterite ?? ""}
              onChange={(e) => setDecl("preterite", e.target.value)}
            />
          </label>
          <label className="card-field">
            <span>Perfekt</span>
            <input
              type="text"
              value={value.declension.perfect ?? ""}
              onChange={(e) => setDecl("perfect", e.target.value)}
            />
          </label>
        </div>
      )}

      <label className="card-field">
        <span>Context Note (optional)</span>
        <textarea
          rows={3}
          value={value.note ?? ""}
          onChange={(e) => set("note", e.target.value || null)}
          placeholder="A short grammar note, if relevant"
        />
      </label>
      {value.note?.trim() && (
        <div className="card-note">
          <Markdown>{value.note}</Markdown>
          {value.section_numbers.length > 0 && (
            <p className="muted card-note-refs">
              Grammar: <SectionRefs refs={value.section_numbers} />
            </p>
          )}
        </div>
      )}
    </>
  );
}
