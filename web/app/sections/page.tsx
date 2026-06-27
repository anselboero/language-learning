import Link from "next/link";
import {
  listChapters,
  listExercises,
  type ChapterWithSections,
  type Exercise,
} from "@/lib/api";
import GrammarBrowser, { type ChapterBundle } from "../components/GrammarBrowser";

export const dynamic = "force-dynamic";

export default async function GrammarPage() {
  let chapters: ChapterWithSections[] = [];
  let exercises: Exercise[] = [];
  let error: string | null = null;
  try {
    [chapters, exercises] = await Promise.all([listChapters(), listExercises()]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not load grammar.";
  }

  // Group exercises by chapter, and include any chapter that only has exercises.
  const byChapter = new Map<number, ChapterWithSections>();
  for (const c of chapters) byChapter.set(c.number, c);
  const exByChapter = new Map<number, Exercise[]>();
  for (const ex of exercises) {
    const list = exByChapter.get(ex.chapter_number) ?? [];
    list.push(ex);
    exByChapter.set(ex.chapter_number, list);
    if (!byChapter.has(ex.chapter_number)) {
      byChapter.set(ex.chapter_number, {
        number: ex.chapter_number,
        title: `Chapter ${ex.chapter_number}`,
        sections: [],
      });
    }
  }
  const allChapters: ChapterBundle[] = [...byChapter.values()]
    .sort((a, b) => a.number - b.number)
    .map((chapter) => ({
      chapter,
      exercises: exByChapter.get(chapter.number) ?? [],
    }));

  return (
    <>
      <h2>Grammar</h2>
      {error && <p className="error">{error}</p>}
      {!error && allChapters.length === 0 && (
        <p className="muted">
          Nothing ingested yet. <Link href="/">Upload</Link> the grammar book and workbook to get started.
        </p>
      )}
      {allChapters.length > 0 && <GrammarBrowser chapters={allChapters} />}
    </>
  );
}
