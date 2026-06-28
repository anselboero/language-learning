"use client";

import { useEffect, useState } from "react";
import type { ChapterWithSections, Exercise } from "@/lib/api";
import ViewToggle, { type ChapterView } from "./ViewToggle";
import ChapterContent from "./ChapterContent";

export interface ChapterBundle {
  chapter: ChapterWithSections;
  exercises: Exercise[];
}

export default function GrammarBrowser({ chapters }: { chapters: ChapterBundle[] }) {
  const [view, setView] = useState<ChapterView>("grammar");
  // Chapters are collapsed by default; this holds the expanded chapter numbers.
  const [open, setOpen] = useState<Set<number>>(new Set());

  const totalSections = chapters.reduce((n, c) => n + c.chapter.sections.length, 0);
  const totalExercises = chapters.reduce((n, c) => n + c.exercises.length, 0);

  const toggle = (number: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(number) ? next.delete(number) : next.add(number);
      return next;
    });

  // Deep-link: arriving with #ex-<id> opens Exercises, expands the chapter that
  // holds it, and scrolls to it.
  useEffect(() => {
    const m = window.location.hash.match(/^#ex-(\d+)$/);
    const id = m ? Number(m[1]) : null;
    const target = id !== null ? chapters.find((c) => c.exercises.some((e) => e.id === id)) : null;
    if (target) {
      setView("exercises");
      setOpen((prev) => new Set(prev).add(target.chapter.number));
      requestAnimationFrame(() =>
        document.getElementById(window.location.hash.slice(1))?.scrollIntoView(),
      );
    }
  }, [chapters]);

  return (
    <>
      <div
        className="row"
        style={{
          justifyContent: "flex-end",
          borderBottom: "1px solid var(--border)",
          paddingBottom: "0.4rem",
          marginBottom: "0.9rem",
        }}
      >
        <ViewToggle
          view={view}
          onChange={setView}
          sectionsCount={totalSections}
          exercisesCount={totalExercises}
        />
      </div>

      {/* Grammar: no chapter header — each chapter's §N section card heads its own
          collapsible tree, so a separate header would just duplicate it. */}
      {view === "grammar"
        ? chapters
            .filter(({ chapter }) => chapter.sections.length > 0)
            .map(({ chapter }) => (
              <section key={chapter.number} style={{ marginBottom: "0.6rem" }}>
                <ChapterContent chapter={chapter} exercises={[]} view="grammar" />
              </section>
            ))
        : chapters.map(({ chapter, exercises }) => {
            const isOpen = open.has(chapter.number);
            return (
              <section key={chapter.number} style={{ marginBottom: isOpen ? "1.5rem" : "0.6rem" }}>
                <h3
                  className="chapter-head"
                  role="button"
                  tabIndex={0}
                  aria-expanded={isOpen}
                  onClick={() => toggle(chapter.number)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggle(chapter.number);
                    }
                  }}
                >
                  <span className="chevron-btn" aria-hidden>
                    {isOpen ? "▾" : "▸"}
                  </span>
                  {chapter.number} · {chapter.title}
                  <span className="muted chapter-count">{exercises.length}</span>
                </h3>
                {isOpen && (
                  <ChapterContent chapter={chapter} exercises={exercises} view="exercises" />
                )}
              </section>
            );
          })}
    </>
  );
}
