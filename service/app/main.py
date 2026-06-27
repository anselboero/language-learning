"""FastAPI entrypoint for the grammar service.

Endpoints:
  POST /ingest/theory             upload Hammer's grammar PDF -> chapters + sections
  POST /ingest/practice           upload the workbook PDF -> exercises linked by section number
  GET  /chapters                  chapters with their nested sections
  GET  /sections                  flat list of all sections
  GET  /sections/{number}         one section
  GET  /sections/{number}/exercises   practice exercises that drill this section
  POST /ask                       word / free-text lookup against the stored grammar
  GET  /health                    liveness probe
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import claude_client, db
from .models import (
    AskRequest,
    AskResponse,
    AssessmentResult,
    AssessRequest,
    ChapterWithSections,
    Exercise,
    GrammarSection,
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
