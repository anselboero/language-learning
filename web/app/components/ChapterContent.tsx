import type { ChapterWithSections, Exercise } from "@/lib/api";
import ExerciseCard from "./ExerciseCard";
import SectionTree from "./SectionTree";
import type { ChapterView } from "./ViewToggle";

export default function ChapterContent({
  chapter,
  exercises,
  view,
}: {
  chapter: ChapterWithSections;
  exercises: Exercise[];
  view: ChapterView;
}) {
  if (view === "grammar") {
    if (chapter.sections.length === 0) {
      return <p className="muted">No grammar sections ingested for this chapter.</p>;
    }
    return <SectionTree sections={chapter.sections} />;
  }

  if (exercises.length === 0) {
    return <p className="muted">No exercises ingested for this chapter.</p>;
  }
  return (
    <>
      {exercises.map((ex) => (
        <ExerciseCard key={ex.id} exercise={ex} />
      ))}
    </>
  );
}
