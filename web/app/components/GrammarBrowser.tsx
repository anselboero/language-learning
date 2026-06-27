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

  const totalSections = chapters.reduce((n, c) => n + c.chapter.sections.length, 0);
  const totalExercises = chapters.reduce((n, c) => n + c.exercises.length, 0);

  // Deep-link: arriving with #ex-<id> opens Exercises and scrolls to it.
  useEffect(() => {
    const m = window.location.hash.match(/^#ex-(\d+)$/);
    const id = m ? Number(m[1]) : null;
    if (id !== null && chapters.some((c) => c.exercises.some((e) => e.id === id))) {
      setView("exercises");
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

      {chapters.map(({ chapter, exercises }) => (
        <section key={chapter.number} style={{ marginBottom: "1.5rem" }}>
          <h3 style={{ marginBottom: "0.6rem" }}>
            {chapter.number} · {chapter.title}
          </h3>
          <ChapterContent chapter={chapter} exercises={exercises} view={view} />
        </section>
      ))}
    </>
  );
}
