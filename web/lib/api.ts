// Thin client for the Python grammar service.
// Override the base URL with NEXT_PUBLIC_GRAMMAR_API if the service runs elsewhere.

export const API_BASE =
  process.env.NEXT_PUBLIC_GRAMMAR_API ?? "http://localhost:8000";

export interface GrammarSection {
  number: string;
  chapter_number: number;
  parent_number: string | null;
  level: number;
  title: string;
  summary: string;
  rule: string;
  examples: string[];
  keywords: string[];
  cross_references: string[];
}

export interface ChapterWithSections {
  number: number;
  title: string;
  sections: GrammarSection[];
}

export interface ExerciseItem {
  prompt: string;
  answer: string | null;
}

export interface Exercise {
  id: number;
  chapter_number: number;
  label: string;
  title: string;
  instructions: string;
  section_refs: string[];
  items: ExerciseItem[];
}

export interface AskResponse {
  answer: string;
  section_numbers: string[];
}

export interface WordForm {
  label: string;
  form: string;
}

export interface DictionaryEntry {
  word: string;
  part_of_speech: string;
  gender: string | null; // display article: der / die / das
  gender_label: string | null; // masculine / feminine / neuter
  pronunciation: string | null;
  forms: WordForm[];
  definitions: string[];
  source_url: string | null;
}

export interface TranslateResponse {
  translation: string;
  note: string | null;
  dictionary: DictionaryEntry | null;
}

export interface ItemAssessment {
  index: number;
  correct: boolean;
  correct_answer: string;
  comment: string;
  section_numbers: string[];
}

export interface AssessmentResult {
  items: ItemAssessment[];
  summary: string;
  review_sections: string[];
}

// --- reading: diglot-weave books -------------------------------------------

export interface WeaveChunk {
  text: string; // verbatim English surface text
  de: string | null; // German form to weave in, or null if not weaveable
  gloss: string | null; // short English meaning shown on tap
  rank: number | null; // 0-based frequency rank (0 = most frequent); null if not weaveable
}

export interface ReadingSegment {
  seq: number;
  english: string;
  german: string;
  chunks: WeaveChunk[];
}

export interface Book {
  id: number;
  title: string;
  author: string;
  vocab_size: number;
  segment_count: number;
}

export interface BookDetail extends Book {
  segments: ReadingSegment[];
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore non-JSON error bodies */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function listChapters(): Promise<ChapterWithSections[]> {
  return unwrap(await fetch(`${API_BASE}/chapters`, { cache: "no-store" }));
}

export async function getSection(number: string): Promise<GrammarSection> {
  return unwrap(
    await fetch(`${API_BASE}/sections/${encodeURIComponent(number)}`, {
      cache: "no-store",
    }),
  );
}

export async function listExercises(): Promise<Exercise[]> {
  return unwrap(await fetch(`${API_BASE}/exercises`, { cache: "no-store" }));
}

export async function getSectionExercises(number: string): Promise<Exercise[]> {
  return unwrap(
    await fetch(`${API_BASE}/sections/${encodeURIComponent(number)}/exercises`, {
      cache: "no-store",
    }),
  );
}

type IngestKind = "theory" | "practice";

export async function ingestPdf(
  kind: IngestKind,
  file: File,
): Promise<Record<string, number>> {
  const form = new FormData();
  form.append("file", file);
  return unwrap(
    await fetch(`${API_BASE}/ingest/${kind}`, { method: "POST", body: form }),
  );
}

export async function assessExercise(
  exerciseId: number,
  answers: { index: number; answer: string }[],
): Promise<AssessmentResult> {
  return unwrap(
    await fetch(`${API_BASE}/exercises/${exerciseId}/assess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    }),
  );
}

export async function askClaude(query: string): Promise<AskResponse> {
  return unwrap(
    await fetch(`${API_BASE}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    }),
  );
}

// --- selection actions -------------------------------------------------------

export async function translateSelection(
  text: string,
  context?: string | null,
): Promise<TranslateResponse> {
  return unwrap(
    await fetch(`${API_BASE}/selection/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context: context ?? null }),
    }),
  );
}

export async function grammarContext(
  text: string,
  context?: string | null,
): Promise<AskResponse> {
  return unwrap(
    await fetch(`${API_BASE}/selection/grammar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context: context ?? null }),
    }),
  );
}

export async function askAboutSelection(
  text: string,
  question: string,
  context?: string | null,
): Promise<AskResponse> {
  return unwrap(
    await fetch(`${API_BASE}/selection/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, question, context: context ?? null }),
    }),
  );
}

export async function listBooks(): Promise<Book[]> {
  return unwrap(await fetch(`${API_BASE}/reading/books`, { cache: "no-store" }));
}

export async function getBook(id: number): Promise<BookDetail> {
  return unwrap(
    await fetch(`${API_BASE}/reading/books/${id}`, { cache: "no-store" }),
  );
}

export async function ingestBook(
  title: string,
  author: string,
  german: File,
  english?: File | null,
): Promise<Book> {
  const form = new FormData();
  form.append("title", title);
  form.append("author", author);
  form.append("german", german);
  if (english) form.append("english", english);
  return unwrap(
    await fetch(`${API_BASE}/reading/ingest`, { method: "POST", body: form }),
  );
}

export async function deleteBook(id: number): Promise<void> {
  await unwrap(
    await fetch(`${API_BASE}/reading/books/${id}`, { method: "DELETE" }),
  );
}
