import Link from "next/link";
import { notFound } from "next/navigation";
import { getSection, getSectionExercises, type Exercise } from "@/lib/api";
import Markdown from "../../components/Markdown";

export const dynamic = "force-dynamic";

export default async function SectionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const number = decodeURIComponent(id);

  const section = await getSection(number).catch(() => null);
  if (!section) notFound();

  let exercises: Exercise[] = [];
  try {
    exercises = await getSectionExercises(number);
  } catch {
    /* exercises are optional — the workbook may not be ingested */
  }

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        <Link href="/sections">← Contents</Link> · Chapter {section.chapter_number}
        {section.parent_number && (
          <>
            {" "}
            · in{" "}
            <Link href={`/sections/${encodeURIComponent(section.parent_number)}`}>
              §{section.parent_number}
            </Link>
          </>
        )}
      </p>
      <h2>
        §{section.number} {section.title}
      </h2>
      <p>{section.summary}</p>

      <div className="card">
        <Markdown>{section.rule}</Markdown>
      </div>

      {exercises.length > 0 && (
        <div className="card">
          <h2>Practice ({exercises.length})</h2>
          <ul>
            {exercises.map((ex) => (
              <li key={ex.id}>
                <Link href={`/sections#ex-${ex.id}`}>
                  Exercise {ex.label} · {ex.title}
                </Link>{" "}
                <span className="muted">— {ex.items.length} items</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {section.cross_references.length > 0 && (
        <p className="muted">
          See also:{" "}
          {section.cross_references.map((ref, i) => (
            <span key={ref}>
              {i > 0 && ", "}
              <Link href={`/sections/${encodeURIComponent(ref)}`}>§{ref}</Link>
            </span>
          ))}
        </p>
      )}
    </>
  );
}
