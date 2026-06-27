"use client";

import { useState } from "react";
import { assessExercise, type AssessmentResult, type Exercise } from "@/lib/api";
import SectionRefs from "./SectionRefs";

export default function ExerciseCard({ exercise }: { exercise: Exercise }) {
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AssessmentResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const verdictByIndex = new Map(result?.items.map((it) => [it.index, it]) ?? []);
  const score = result
    ? result.items.filter((it) => it.correct).length
    : null;

  async function handleCheck() {
    setBusy(true);
    setError(null);
    try {
      const payload = exercise.items.map((_, i) => ({
        index: i,
        answer: answers[i] ?? "",
      }));
      setResult(await assessExercise(exercise.id, payload));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Assessment failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" id={`ex-${exercise.id}`}>
      <h2 style={{ marginBottom: "0.25rem" }}>
        {exercise.label} · {exercise.title}
      </h2>
      {exercise.section_refs.length > 0 && (
        <p className="muted" style={{ marginTop: 0 }}>
          Practises <SectionRefs refs={exercise.section_refs} />
        </p>
      )}
      <p>{exercise.instructions}</p>

      <ol>
        {exercise.items.map((item, i) => {
          const v = verdictByIndex.get(i);
          return (
            <li key={i} style={{ marginBottom: "0.7rem" }}>
              <div>{item.prompt}</div>
              <div className="row" style={{ marginTop: "0.25rem" }}>
                <input
                  type="text"
                  value={answers[i] ?? ""}
                  placeholder="your answer"
                  disabled={busy}
                  onChange={(e) =>
                    setAnswers((prev) => ({ ...prev, [i]: e.target.value }))
                  }
                  style={{
                    maxWidth: "18rem",
                    borderColor: v
                      ? v.correct
                        ? "#2e7d32"
                        : "#b3261e"
                      : undefined,
                  }}
                />
                {v && <span>{v.correct ? "✓" : "✗"}</span>}
              </div>
              {v && !v.correct && (
                <div className="muted" style={{ fontSize: "0.9rem", marginTop: "0.2rem" }}>
                  Correct: <strong>{v.correct_answer}</strong>
                  {v.comment ? ` — ${v.comment}` : ""}
                  {v.section_numbers.length > 0 && (
                    <>
                      {" "}
                      (<SectionRefs refs={v.section_numbers} />)
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <div className="row">
        <button onClick={handleCheck} disabled={busy}>
          {busy ? "Checking…" : result ? "Check again" : "Check answers"}
        </button>
        {score !== null && (
          <span className="muted">
            Score: {score}/{exercise.items.length}
          </span>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="answer" style={{ marginTop: "1rem" }}>
          <p style={{ marginTop: 0 }}>{result.summary}</p>
          {result.review_sections.length > 0 && (
            <p className="muted" style={{ marginBottom: 0 }}>
              Review: <SectionRefs refs={result.review_sections} />
            </p>
          )}
        </div>
      )}
    </div>
  );
}
