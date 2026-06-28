import Link from "next/link";
import { getChapter, type ChapterDetail } from "@/lib/api";
import Reader from "../../../components/Reader";

export const dynamic = "force-dynamic";

export default async function ChapterPage({
  params,
}: {
  params: Promise<{ id: string; chapter: string }>;
}) {
  const { id, chapter } = await params;
  const bookId = Number(id);
  let data: ChapterDetail | null = null;
  let error: string | null = null;
  try {
    data = await getChapter(bookId, Number(chapter));
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not load this chapter.";
  }

  return (
    <>
      <p style={{ marginTop: 0 }}>
        <Link href={`/reading/${bookId}`}>← {data?.book_title ?? "Contents"}</Link>
      </p>
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <h2 style={{ marginBottom: "0.8rem" }}>{data.title}</h2>
          <Reader bookId={bookId} segments={data.segments} />
          <ChapterNav bookId={bookId} prev={data.prev_idx} next={data.next_idx} />
        </>
      )}
    </>
  );
}

function ChapterNav({
  bookId,
  prev,
  next,
}: {
  bookId: number;
  prev: number | null;
  next: number | null;
}) {
  return (
    <div className="chapter-nav">
      {prev !== null ? (
        <Link href={`/reading/${bookId}/${prev}`}>← Previous chapter</Link>
      ) : (
        <span />
      )}
      {next !== null && (
        <Link href={`/reading/${bookId}/${next}`}>Next chapter →</Link>
      )}
    </div>
  );
}
