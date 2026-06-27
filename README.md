# Language Learning

A platform for improving language skills through gamified, science-based activities —
grammar, listening, writing, and vocabulary, glued together by ANKI-style flashcards.

This repo currently implements the **Grammar** module (MVP), built around
**Hammer's German Grammar and Usage** (theory) and **Practising German Grammar**
(the cross-referenced workbook):

- Ingest the theory PDF → Claude extracts chapters and sections using the book's
  own decimal numbering (`12`, `12.3`, `12.3.2`), preserving rules and examples.
- Ingest the workbook PDF → exercises are linked to the Hammer's section number
  they drill, so each section shows its own practice.
- Browse the grammar as a chapter → section tree.
- **Ask Claude**: type a German word or free-text question and get pointed to the
  relevant numbered rule.

The DB schema mirrors the book's structure: `chapters` → `sections` (keyed by the
decimal number) → `exercises` (keyed by the section they practise).

Local-first, single-user, no auth. Ingestion is an occasional batch step.

## Architecture

```
web/      Next.js (App Router, TypeScript) — the UI
service/  Python FastAPI — PDF ingestion + grammar lookup via the Claude API
          SQLite (grammar.db) for local-first storage
```

The web app talks to the Python service over HTTP (CORS allows `localhost:3000`).

## Prerequisites

- Python 3.11+
- Node.js 20+
- An Anthropic API key

## Run the grammar service

```bash
cd service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY=sk-ant-...   # or rely on your shell/profile
uvicorn app.main:app --reload --port 8000
```

The SQLite DB (`service/grammar.db`) is created on first run.

## Run the web app

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

Set `NEXT_PUBLIC_GRAMMAR_API` in `web/.env.local` if the service isn't on `localhost:8000`.

## API (grammar service)

| Method | Path                          | Purpose                                                   |
| ------ | ----------------------------- | --------------------------------------------------------- |
| POST   | `/ingest/theory`              | Upload Hammer's PDF; extract chapters + sections.         |
| POST   | `/ingest/practice`            | Upload the workbook PDF; extract exercises by section.    |
| GET    | `/chapters`                   | Chapters with their nested sections.                      |
| GET    | `/sections`                   | Flat list of all sections.                                |
| GET    | `/sections/{number}`          | Fetch one section (e.g. `/sections/12.3`).                |
| GET    | `/exercises`                  | All workbook exercises (units), ordered by chapter.       |
| GET    | `/sections/{number}/exercises`| Exercises whose GGU range covers this section.            |
| POST   | `/exercises/{id}/assess`      | Grade submitted answers; cite sections for mistakes.      |
| POST   | `/ask`                        | Word / free-text lookup against stored grammar.           |

## Notes & next steps

- Ingestion (model `claude-opus-4-8`) slices the PDF into page windows (`INGEST_PAGE_WINDOW`,
  default 12) and merges sections across windows by their decimal number — a full ~600-page
  book is many calls and takes several minutes, but only runs once.
- Planned modules: flashcards (the cross-cutting "glue"), listening, writing, vocabulary,
  and gamification.
