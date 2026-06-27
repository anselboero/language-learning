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
