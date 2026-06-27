"use client";

export type ChapterView = "grammar" | "exercises";

export default function ViewToggle({
  view,
  onChange,
  sectionsCount,
  exercisesCount,
}: {
  view: ChapterView;
  onChange: (v: ChapterView) => void;
  sectionsCount: number;
  exercisesCount: number;
}) {
  return (
    <div className="toggle">
      <button
        className={view === "grammar" ? "seg active" : "seg"}
        onClick={() => onChange("grammar")}
        disabled={sectionsCount === 0}
      >
        Grammar
      </button>
      <button
        className={view === "exercises" ? "seg active" : "seg"}
        onClick={() => onChange("exercises")}
        disabled={exercisesCount === 0}
      >
        Exercises ({exercisesCount})
      </button>
    </div>
  );
}
