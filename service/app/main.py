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
  POST /reading/ingest            turn a German text (English optional) into a parallel book
  GET  /reading/books             list ingested reading books
  GET  /reading/books/{id}        one book's table of contents (chapters)
  GET  /reading/books/{id}/chapters/{idx}   one chapter's aligned segments + navigation
  DELETE /reading/books/{id}      remove a reading book
  GET  /health                    liveness probe
"""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from . import claude_client, db, dictionary, srs
from .models import (
    AskRequest,
    AskResponse,
    AssessmentResult,
    AssessRequest,
    AnswerCheck,
    AnswerCheckRequest,
    Book,
    BookDetail,
    CardSuggestion,
    CardSuggestRequest,
    ChapterDetail,
    ChapterWithSections,
    DictationCheckRequest,
    Exercise,
    Flashcard,
    FlashcardData,
    FreeAskRequest,
    GrammarContextRequest,
    GrammarSection,
    ListeningClip,
    ListeningClipData,
    ListeningSource,
    ReviewItem,
    ReviewRequest,
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
    return Book(**book.model_dump(exclude={"chapters"}))


@app.get("/reading/books", response_model=list[Book])
def reading_books() -> list[Book]:
    return db.list_books()


@app.get("/reading/books/{book_id}", response_model=BookDetail)
def reading_book(book_id: int) -> BookDetail:
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found.")
    return book


@app.get("/reading/books/{book_id}/chapters/{idx}", response_model=ChapterDetail)
def reading_chapter(book_id: int, idx: int) -> ChapterDetail:
    chapter = db.get_chapter(book_id, idx)
    if not chapter:
        raise HTTPException(404, "Chapter not found.")
    return chapter


@app.delete("/reading/books/{book_id}")
def delete_reading_book(book_id: int) -> dict[str, bool]:
    if not db.delete_book(book_id):
        raise HTTPException(404, "Book not found.")
    return {"deleted": True}


# --- flashcards --------------------------------------------------------------


@app.post("/flashcards/generate", response_model=CardSuggestion)
def generate_flashcard(req: CardSuggestRequest) -> CardSuggestion:
    """Propose an editable card for a highlighted word; nothing is persisted yet."""
    try:
        return claude_client.suggest_flashcard(req.german, req.target, req.english, db.list_sections())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Card generation failed: {exc}") from exc


@app.post("/flashcards", response_model=Flashcard)
def create_flashcard(card: FlashcardData) -> Flashcard:
    return db.create_flashcard(card)


@app.get("/flashcards", response_model=list[Flashcard])
def list_flashcards() -> list[Flashcard]:
    return db.list_flashcards()


@app.get("/flashcards/due", response_model=list[Flashcard])
def due_flashcards() -> list[Flashcard]:
    return db.list_due_flashcards(srs._today().isoformat())


@app.put("/flashcards/{card_id}", response_model=Flashcard)
def update_flashcard(card_id: int, card: FlashcardData) -> Flashcard:
    updated = db.update_flashcard(card_id, card)
    if not updated:
        raise HTTPException(404, "Flashcard not found.")
    return updated


@app.post("/flashcards/{card_id}/review", response_model=Flashcard)
def review_flashcard(card_id: int, req: ReviewRequest) -> Flashcard:
    try:
        updated = db.review_flashcard(card_id, req.rating)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, "Flashcard not found.")
    return updated


@app.post("/flashcards/{card_id}/check", response_model=AnswerCheck)
def check_flashcard_answer(card_id: int, req: AnswerCheckRequest) -> AnswerCheck:
    card = db.get_flashcard(card_id)
    if not card:
        raise HTTPException(404, "Flashcard not found.")
    try:
        return claude_client.check_answer(card.english, card.german, req.answer, db.list_sections())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Answer check failed: {exc}") from exc


@app.delete("/flashcards/{card_id}")
def delete_flashcard(card_id: int) -> dict[str, bool]:
    if not db.delete_flashcard(card_id):
        raise HTTPException(404, "Flashcard not found.")
    return {"deleted": True}


# --- listening: sources, dictation clips, media ------------------------------

# Uploaded videos are copied here and streamed back by range; the originals on the
# learner's disk are left alone. Configurable, defaults to service/media.
MEDIA_DIR = os.environ.get(
    "GRAMMAR_MEDIA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "media")
)


def _save_media(upload: UploadFile) -> str:
    """Stream an uploaded media file into MEDIA_DIR, returning its stored path.

    The filename is sanitised and de-duplicated so two uploads never collide. The
    file is copied in chunks rather than read whole, so large videos don't blow up
    memory.
    """
    os.makedirs(MEDIA_DIR, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(upload.filename or "")) or "video"
    stem, ext = os.path.splitext(name)
    dest = os.path.join(MEDIA_DIR, name)
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(MEDIA_DIR, f"{stem}_{i}{ext}")
        i += 1
    with open(dest, "wb") as out:
        shutil.copyfileobj(upload.file, out, length=1024 * 1024)
    return dest


@app.post("/listening/ingest", response_model=ListeningSource)
async def ingest_listening(
    title: str = Form(...),
    video: UploadFile = File(...),
    srt: UploadFile = File(...),
) -> ListeningSource:
    """Curate an uploaded video + its SRT into dictation clips."""
    srt_text = await _read_text(srt)
    video_path = _save_media(video)
    try:
        clips = claude_client.curate_clips(srt_text)
    except Exception as exc:  # noqa: BLE001
        # Don't leave an orphan video behind if curation fails.
        try:
            os.remove(video_path)
        except OSError:
            pass
        raise HTTPException(502, f"Listening ingestion failed: {exc}") from exc
    return db.create_listening_source(title, video_path, clips)


@app.get("/listening/sources", response_model=list[ListeningSource])
def listening_sources() -> list[ListeningSource]:
    return db.list_listening_sources()


@app.get("/listening/sources/{source_id}/clips", response_model=list[ListeningClip])
def listening_source_clips(source_id: int) -> list[ListeningClip]:
    if not db.get_listening_source(source_id):
        raise HTTPException(404, "Listening source not found.")
    return db.list_clips_for_source(source_id)


@app.delete("/listening/sources/{source_id}")
def delete_listening_source(source_id: int) -> dict[str, bool]:
    source = db.get_listening_source(source_id)
    if not source:
        raise HTTPException(404, "Listening source not found.")
    db.delete_listening_source(source_id)
    # Remove our stored copy too — but only files that live inside MEDIA_DIR.
    if os.path.abspath(source.video_path).startswith(os.path.abspath(MEDIA_DIR) + os.sep):
        try:
            os.remove(source.video_path)
        except OSError:
            pass
    return {"deleted": True}


@app.get("/listening/clips", response_model=list[ListeningClip])
def listening_clips() -> list[ListeningClip]:
    return db.list_clips()


@app.put("/listening/clips/{clip_id}", response_model=ListeningClip)
def update_listening_clip(clip_id: int, clip: ListeningClipData) -> ListeningClip:
    updated = db.update_listening_clip(clip_id, clip)
    if not updated:
        raise HTTPException(404, "Clip not found.")
    return updated


@app.post("/listening/clips/{clip_id}/review", response_model=ListeningClip)
def review_listening_clip(clip_id: int, req: ReviewRequest) -> ListeningClip:
    try:
        updated = db.review_listening_clip(clip_id, req.rating)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, "Clip not found.")
    return updated


@app.post("/listening/clips/{clip_id}/check", response_model=AnswerCheck)
def check_dictation(clip_id: int, req: DictationCheckRequest) -> AnswerCheck:
    clip = db.get_listening_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found.")
    try:
        return claude_client.check_dictation(
            clip.transcript_de, clip.transcript_en, req.answer, db.list_sections()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Dictation check failed: {exc}") from exc


@app.delete("/listening/clips/{clip_id}")
def delete_listening_clip(clip_id: int) -> dict[str, bool]:
    if not db.delete_listening_clip(clip_id):
        raise HTTPException(404, "Clip not found.")
    return {"deleted": True}


_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _media_response(path: str, request: Request) -> Response:
    """Serve a local media file, honouring HTTP Range so the player can seek."""
    if not os.path.isfile(path):
        raise HTTPException(404, "Media file is no longer on disk.")
    file_size = os.path.getsize(path)
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    headers = {"accept-ranges": "bytes"}

    start, end, status = 0, file_size - 1, 200
    range_header = request.headers.get("range")
    if range_header and (m := _RANGE.match(range_header.strip())):
        g1, g2 = m.group(1), m.group(2)
        if g1 == "" and g2:  # suffix range: last N bytes
            start, end = max(0, file_size - int(g2)), file_size - 1
        else:
            start = int(g1) if g1 else 0
            end = int(g2) if g2 else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            return Response(
                status_code=416, headers={"content-range": f"bytes */{file_size}"}
            )
        status = 206
        headers["content-range"] = f"bytes {start}-{end}/{file_size}"

    length = end - start + 1
    headers["content-length"] = str(length)

    def stream(chunk_size: int = 1024 * 1024):
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(stream(), status_code=status, headers=headers, media_type=media_type)


@app.get("/listening/sources/{source_id}/media")
def listening_media(source_id: int, request: Request) -> Response:
    source = db.get_listening_source(source_id)
    if not source:
        raise HTTPException(404, "Listening source not found.")
    return _media_response(source.video_path, request)


# --- unified review feed -----------------------------------------------------


@app.get("/review/due", response_model=list[ReviewItem])
def review_due() -> list[ReviewItem]:
    """The mixed review queue: due vocab cards and listening clips, oldest-due first."""
    today = srs._today().isoformat()
    items = [ReviewItem(kind="vocab", due=c.due, vocab=c) for c in db.list_due_flashcards(today)]
    items += [ReviewItem(kind="listening", due=c.due, listening=c) for c in db.list_due_clips(today)]
    items.sort(key=lambda i: i.due)
    return items
