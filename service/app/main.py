"""FastAPI entrypoint for the grammar service.

Endpoints:
  POST /ingest/theory             upload Hammer's grammar PDF -> chapters + sections
  POST /ingest/practice           upload the workbook PDF -> exercises linked by section number
  GET  /chapters                  chapters with their nested sections
  GET  /sections                  flat list of all sections
  GET  /sections/{number}         one section
  GET  /sections/{number}/exercises   practice exercises that drill this section
  POST /ask                       word / free-text lookup against the stored grammar
  POST /selection/translate       translate a highlighted span (+ dictionary facts)
  POST /selection/grammar         grammar explanation of a span, grounded in sections
  POST /selection/ask             free-form question about a span, citing sections
  POST /reading/ingest            turn a German text (English optional) into a diglot book
  GET  /reading/books             list ingested reading books
  GET  /reading/books/{id}        one book with its aligned, weave-ready segments
  DELETE /reading/books/{id}      remove a reading book
  GET  /health                    liveness probe
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import claude_client, db, dictionary
from .models import (
    AskRequest,
    AskResponse,
    AssessmentResult,
    AssessRequest,
    Book,
    BookDetail,
    ChapterWithSections,
    Exercise,
    FreeAskRequest,
    GrammarContextRequest,
    GrammarSection,
    TranslateRequest,
    TranslateResponse,
)

app = FastAPI(title="Language Learning — Grammar Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def _read_pdf(file: UploadFile) -> bytes:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    return data


@app.post("/ingest/theory")
async def ingest_theory(file: UploadFile = File(...)) -> dict[str, int]:
    pdf_bytes = await _read_pdf(file)
    try:
        return claude_client.ingest_theory_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 — surface ingestion failures to the client
        raise HTTPException(502, f"Theory ingestion failed: {exc}") from exc


@app.post("/ingest/practice")
async def ingest_practice(file: UploadFile = File(...)) -> dict[str, int]:
    pdf_bytes = await _read_pdf(file)
    try:
        stored = claude_client.ingest_practice_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Practice ingestion failed: {exc}") from exc
    return {"exercises_stored": stored}


@app.get("/chapters", response_model=list[ChapterWithSections])
def chapters() -> list[ChapterWithSections]:
    return db.list_chapters_with_sections()


@app.get("/sections", response_model=list[GrammarSection])
def sections() -> list[GrammarSection]:
    return db.list_sections()


@app.get("/sections/{number}", response_model=GrammarSection)
def section(number: str) -> GrammarSection:
    found = db.get_section(number)
    if not found:
        raise HTTPException(404, "Section not found.")
    return found


@app.get("/exercises", response_model=list[Exercise])
def exercises() -> list[Exercise]:
    return db.list_exercises()


@app.get("/sections/{number}/exercises", response_model=list[Exercise])
def section_exercises(number: str) -> list[Exercise]:
    return db.get_exercises_for_section(number)


@app.post("/exercises/{exercise_id}/assess", response_model=AssessmentResult)
def assess(exercise_id: int, req: AssessRequest) -> AssessmentResult:
    exercise = db.get_exercise(exercise_id)
    if not exercise:
        raise HTTPException(404, "Exercise not found.")
    sections = db.get_sections_for_exercise(exercise.section_refs)
    try:
        return claude_client.assess(exercise, req.answers, sections)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Assessment failed: {exc}") from exc


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    try:
        return claude_client.ask(req.query, db.list_sections())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lookup failed: {exc}") from exc


# --- selection actions (translate / grammar context / free question) ---------


@app.post("/selection/translate", response_model=TranslateResponse)
def selection_translate(req: TranslateRequest) -> TranslateResponse:
    try:
        out = claude_client.translate_selection(req.text, req.context)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Translation failed: {exc}") from exc
    # Dictionary facts are best-effort and only apply to single words; never fatal.
    return TranslateResponse(
        translation=out.translation,
        note=out.note,
        dictionary=dictionary.lookup(req.text),
    )


@app.post("/selection/grammar", response_model=AskResponse)
def selection_grammar(req: GrammarContextRequest) -> AskResponse:
    try:
        return claude_client.explain_grammar(req.text, req.context, db.list_sections())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lookup failed: {exc}") from exc


@app.post("/selection/ask", response_model=AskResponse)
def selection_ask(req: FreeAskRequest) -> AskResponse:
    try:
        return claude_client.ask_free(req.text, req.question, req.context, db.list_sections())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lookup failed: {exc}") from exc


# --- reading -----------------------------------------------------------------


async def _read_text(file: UploadFile) -> str:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "Could not decode the text file.") from exc
    if not text.strip():
        raise HTTPException(400, "Uploaded file has no text.")
    return text


@app.post("/reading/ingest", response_model=Book)
async def ingest_book(
    title: str = Form(...),
    author: str = Form(""),
    german: UploadFile = File(...),
    english: Optional[UploadFile] = File(None),
) -> Book:
    german_text = await _read_text(german)
    # English is optional: with German alone, Claude generates the aligned scaffold.
    english_text = await _read_text(english) if english is not None else None
    try:
        book_id = claude_client.ingest_book(title, author, german_text, english_text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Ingestion failed: {exc}") from exc
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(500, "Book was ingested but could not be loaded.")
    return Book(**book.model_dump(exclude={"segments"}))


@app.get("/reading/books", response_model=list[Book])
def reading_books() -> list[Book]:
    return db.list_books()


@app.get("/reading/books/{book_id}", response_model=BookDetail)
def reading_book(book_id: int) -> BookDetail:
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found.")
    return book


@app.delete("/reading/books/{book_id}")
def delete_reading_book(book_id: int) -> dict[str, bool]:
    if not db.delete_book(book_id):
        raise HTTPException(404, "Book not found.")
    return {"deleted": True}
