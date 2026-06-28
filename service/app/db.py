"""Local-first SQLite storage, structured like Hammer's German Grammar.

Three tables mirror the book: ``chapters`` → ``sections`` (keyed by the book's
decimal number) → ``exercises`` (keyed by the section number they practise).
Theory and practice are ingested separately and join on the section number.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from . import srs
from .models import (
    Book,
    BookDetail,
    CardDeclension,
    Chapter,
    ChapterDetail,
    ChapterMeta,
    ChapterWithSections,
    Exercise,
    ExerciseData,
    ExerciseItem,
    Flashcard,
    FlashcardData,
    GrammarSection,
    ReadingChapter,
    ReadingSegment,
)

DB_PATH = os.environ.get(
    "GRAMMAR_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "grammar.db"),
)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chapters (
                number INTEGER PRIMARY KEY,
                title  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sections (
                number          TEXT PRIMARY KEY,        -- e.g. '12.3.2'
                chapter_number  INTEGER NOT NULL,
                parent_number   TEXT,                    -- e.g. '12.3', NULL at chapter level
                level           INTEGER NOT NULL,        -- segment count
                title           TEXT NOT NULL,
                summary         TEXT NOT NULL,
                rule            TEXT NOT NULL,
                examples        TEXT NOT NULL,           -- JSON array of strings
                keywords        TEXT NOT NULL,           -- JSON array of strings
                cross_references TEXT NOT NULL           -- JSON array of section numbers
            );

            CREATE TABLE IF NOT EXISTS exercises (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_number INTEGER NOT NULL,
                label          TEXT NOT NULL,           -- exercise number as printed, e.g. '5'
                title          TEXT NOT NULL,
                instructions   TEXT NOT NULL,
                section_refs   TEXT NOT NULL,           -- JSON list of GGU refs (numbers or ranges)
                items          TEXT NOT NULL            -- JSON list of {prompt, answer}
            );

            CREATE TABLE IF NOT EXISTS books (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                author      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reading_segments (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id  INTEGER NOT NULL,
                seq      INTEGER NOT NULL,             -- order within the book
                english  TEXT NOT NULL,
                german   TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reading_chapters (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id    INTEGER NOT NULL,
                idx        INTEGER NOT NULL,           -- order within the book
                title      TEXT NOT NULL,
                start_seq  INTEGER NOT NULL,           -- seq of the chapter's first segment
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS flashcards (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id         INTEGER,                  -- source reading book, or NULL
                english         TEXT NOT NULL,            -- front: English sentence
                german          TEXT NOT NULL,            -- back: German sentence
                target_de       TEXT NOT NULL,            -- highlighted German word
                target_en       TEXT NOT NULL,            -- highlighted English word(s)
                pos             TEXT NOT NULL,            -- 'noun' | 'verb' | 'other'
                lemma           TEXT NOT NULL,            -- 'das Gemüse' / 'mögen'
                declension      TEXT NOT NULL,            -- JSON {gender, plural, infinitive, preterite, perfect}
                note            TEXT,                     -- optional grammar Context Note (Markdown)
                section_numbers TEXT NOT NULL,            -- JSON list of grammar refs
                due             TEXT NOT NULL,            -- ISO date next due
                interval        REAL NOT NULL,            -- days
                ease            REAL NOT NULL,            -- SM-2 ease factor
                reps            INTEGER NOT NULL,
                lapses          INTEGER NOT NULL,
                last_reviewed   TEXT,                     -- ISO datetime or NULL
                created_at      TEXT NOT NULL,            -- ISO datetime
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(chapter_number);
            CREATE INDEX IF NOT EXISTS idx_exercises_chapter ON exercises(chapter_number);
            CREATE INDEX IF NOT EXISTS idx_segments_book ON reading_segments(book_id);
            CREATE INDEX IF NOT EXISTS idx_chapters_book ON reading_chapters(book_id);
            CREATE INDEX IF NOT EXISTS idx_flashcards_due ON flashcards(due);
            """
        )
        # Drop the legacy diglot-weave columns from a pre-existing DB. The German and
        # English text we still use are already stored, so books carry over intact.
        _drop_column_if_present(conn, "books", "vocab_size")
        _drop_column_if_present(conn, "reading_segments", "chunks")


def _drop_column_if_present(conn: sqlite3.Connection, table: str, column: str) -> None:
    """Idempotently drop ``column`` from ``table`` if the schema still has it."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


# --- ordering helper ---------------------------------------------------------


def _sort_key(number: str) -> tuple[int, ...]:
    """Numeric sort for decimal section numbers so 12.10 follows 12.9."""
    parts: list[int] = []
    for seg in number.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# --- writes ------------------------------------------------------------------


def upsert_theory(chapters: list[Chapter], sections: list[GrammarSection]) -> dict[str, int]:
    """Add or update the theory in this ingestion, preserving other chapters.

    Ingestion is incremental: you can upload one chapter at a time and earlier
    chapters stay. Only the chapters PRESENT in this batch are refreshed — their
    old sections are cleared first so a re-ingest of a chapter replaces it cleanly
    (rather than leaving stale sections behind), while untouched chapters remain.
    """
    touched = {c.number for c in chapters} | {s.chapter_number for s in sections}
    with _connect() as conn:
        for chapter_number in touched:
            conn.execute("DELETE FROM sections WHERE chapter_number = ?", (chapter_number,))
        for c in chapters:
            conn.execute(
                "INSERT OR REPLACE INTO chapters (number, title) VALUES (?, ?)",
                (c.number, c.title),
            )
        for s in sections:
            conn.execute(
                """
                INSERT OR REPLACE INTO sections
                    (number, chapter_number, parent_number, level, title, summary,
                     rule, examples, keywords, cross_references)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s.number,
                    s.chapter_number,
                    s.parent_number,
                    s.level,
                    s.title,
                    s.summary,
                    s.rule,
                    json.dumps(s.examples),
                    json.dumps(s.keywords),
                    json.dumps(s.cross_references),
                ),
            )
    return {"chapters": len(chapters), "sections": len(sections)}


def upsert_practice(exercises: list[ExerciseData]) -> int:
    """Add or update the exercises in this ingestion, preserving other chapters.

    Like theory ingestion, practice is incremental: uploading one workbook chapter
    keeps the others. Only the chapters PRESENT in this batch are refreshed — their
    old exercises are cleared first so a re-ingest replaces a chapter cleanly (no
    duplicates), while untouched chapters remain.
    """
    touched = {e.chapter_number for e in exercises}
    with _connect() as conn:
        for chapter_number in touched:
            conn.execute("DELETE FROM exercises WHERE chapter_number = ?", (chapter_number,))
        for e in exercises:
            conn.execute(
                """
                INSERT INTO exercises (chapter_number, label, title, instructions, section_refs, items)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    e.chapter_number,
                    e.label,
                    e.title,
                    e.instructions,
                    json.dumps(e.section_refs),
                    json.dumps([i.model_dump() for i in e.items]),
                ),
            )
    return len(exercises)


def _ref_covers(ref: str, target: str) -> bool:
    """Does a printed GGU reference (single number or range) cover `target`?"""
    for dash in ("–", "—", "-"):  # en dash, em dash, hyphen
        if dash in ref:
            start, _, end = ref.partition(dash)
            return _sort_key(start.strip()) <= _sort_key(target) <= _sort_key(end.strip())
    return ref.strip() == target


# --- reads -------------------------------------------------------------------


def _row_to_section(row: sqlite3.Row) -> GrammarSection:
    return GrammarSection(
        number=row["number"],
        chapter_number=row["chapter_number"],
        parent_number=row["parent_number"],
        level=row["level"],
        title=row["title"],
        summary=row["summary"],
        rule=row["rule"],
        examples=json.loads(row["examples"]),
        keywords=json.loads(row["keywords"]),
        cross_references=json.loads(row["cross_references"]),
    )


def list_sections() -> list[GrammarSection]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM sections").fetchall()
    return sorted((_row_to_section(r) for r in rows), key=lambda s: _sort_key(s.number))


def get_section(number: str) -> GrammarSection | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sections WHERE number = ?", (number,)).fetchone()
    return _row_to_section(row) if row else None


def list_chapters_with_sections() -> list[ChapterWithSections]:
    with _connect() as conn:
        chapter_rows = conn.execute("SELECT * FROM chapters ORDER BY number").fetchall()
    sections = list_sections()

    by_chapter: dict[int, list[GrammarSection]] = {}
    for s in sections:
        by_chapter.setdefault(s.chapter_number, []).append(s)

    result = [
        ChapterWithSections(number=c["number"], title=c["title"], sections=by_chapter.pop(c["number"], []))
        for c in chapter_rows
    ]

    # Sections whose chapter heading wasn't captured still get surfaced.
    for chapter_number, secs in sorted(by_chapter.items()):
        result.append(
            ChapterWithSections(
                number=chapter_number,
                title=f"Chapter {chapter_number}",
                sections=secs,
            )
        )
    result.sort(key=lambda c: c.number)
    return result


def _row_to_exercise(row: sqlite3.Row) -> Exercise:
    return Exercise(
        id=row["id"],
        chapter_number=row["chapter_number"],
        label=row["label"],
        title=row["title"],
        instructions=row["instructions"],
        section_refs=json.loads(row["section_refs"]),
        items=[ExerciseItem(**i) for i in json.loads(row["items"])],
    )


def list_exercises() -> list[Exercise]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM exercises").fetchall()
    exercises = [_row_to_exercise(r) for r in rows]
    # Order by chapter, then numerically by label where possible.
    def label_key(e: Exercise) -> tuple[int, str]:
        try:
            return (int(e.label), "")
        except ValueError:
            return (10**9, e.label)

    return sorted(exercises, key=lambda e: (e.chapter_number, label_key(e)))


def get_exercise(exercise_id: int) -> Exercise | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    return _row_to_exercise(row) if row else None


def get_exercises_for_section(number: str) -> list[Exercise]:
    """Exercises whose printed GGU references cover this section number."""
    return [
        e
        for e in list_exercises()
        if any(_ref_covers(ref, number) for ref in e.section_refs)
    ]


def get_sections_for_exercise(refs: list[str]) -> list[GrammarSection]:
    """Sections whose number falls within any of an exercise's GGU references."""
    return [
        s
        for s in list_sections()
        if any(_ref_covers(ref, s.number) for ref in refs)
    ]


# --- reading: books + aligned segments ---------------------------------------


def create_book(
    title: str,
    author: str,
    segments: list[ReadingSegment],
    chapters: list[ReadingChapter] | None = None,
) -> int:
    """Store a freshly-aligned book, its segments, and any chapters; returns the book id."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author) VALUES (?, ?)",
            (title, author),
        )
        book_id = int(cur.lastrowid)
        for seg in segments:
            conn.execute(
                """
                INSERT INTO reading_segments (book_id, seq, english, german)
                VALUES (?, ?, ?, ?)
                """,
                (book_id, seg.seq, seg.english, seg.german),
            )
        for ch in chapters or []:
            conn.execute(
                """
                INSERT INTO reading_chapters (book_id, idx, title, start_seq)
                VALUES (?, ?, ?, ?)
                """,
                (book_id, ch.idx, ch.title, ch.start_seq),
            )
    return book_id


def _chapter_rows(conn: sqlite3.Connection, book_id: int) -> list[ReadingChapter]:
    rows = conn.execute(
        "SELECT idx, title, start_seq FROM reading_chapters WHERE book_id = ? ORDER BY idx",
        (book_id,),
    ).fetchall()
    return [
        ReadingChapter(idx=r["idx"], title=r["title"], start_seq=r["start_seq"]) for r in rows
    ]


def _chapter_metas(
    chapters: list[ReadingChapter], total_segments: int, book_title: str
) -> list[ChapterMeta]:
    """The table of contents. A book with no stored chapters reads as one chapter."""
    if not chapters:
        return [ChapterMeta(idx=0, title=book_title, segment_count=total_segments)]
    bounds = [c.start_seq for c in chapters] + [total_segments]
    return [
        ChapterMeta(idx=c.idx, title=c.title, segment_count=bounds[i + 1] - c.start_seq)
        for i, c in enumerate(chapters)
    ]


def list_books() -> list[Book]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT b.id, b.title, b.author,
                   (SELECT COUNT(*) FROM reading_segments s WHERE s.book_id = b.id) AS segment_count,
                   (SELECT COUNT(*) FROM reading_chapters c WHERE c.book_id = b.id) AS chapter_count
            FROM books b
            ORDER BY b.id
            """
        ).fetchall()
    return [
        Book(
            id=r["id"],
            title=r["title"],
            author=r["author"],
            segment_count=r["segment_count"],
            chapter_count=max(r["chapter_count"], 1),
        )
        for r in rows
    ]


def get_book(book_id: int) -> BookDetail | None:
    """A book's table of contents (chapters), without the segment text itself."""
    with _connect() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            return None
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM reading_segments WHERE book_id = ?", (book_id,)
        ).fetchone()["n"]
        chapters = _chapter_rows(conn, book_id)
    metas = _chapter_metas(chapters, total, book["title"])
    return BookDetail(
        id=book["id"],
        title=book["title"],
        author=book["author"],
        segment_count=total,
        chapter_count=len(metas),
        chapters=metas,
    )


def get_chapter(book_id: int, idx: int) -> ChapterDetail | None:
    """One chapter's segments plus navigation to its neighbours.

    With no stored chapters the whole book is chapter 0; otherwise a chapter spans from
    its ``start_seq`` to the next chapter's (or the end of the book).
    """
    with _connect() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            return None
        chapters = _chapter_rows(conn, book_id)
        count = len(chapters) or 1
        if idx < 0 or idx >= count:
            return None

        if chapters:
            start = chapters[idx].start_seq
            end = chapters[idx + 1].start_seq if idx + 1 < len(chapters) else None
            title = chapters[idx].title
        else:
            start, end, title = 0, None, book["title"]

        if end is None:
            seg_rows = conn.execute(
                "SELECT * FROM reading_segments WHERE book_id = ? AND seq >= ? ORDER BY seq",
                (book_id, start),
            ).fetchall()
        else:
            seg_rows = conn.execute(
                "SELECT * FROM reading_segments WHERE book_id = ? AND seq >= ? AND seq < ? ORDER BY seq",
                (book_id, start, end),
            ).fetchall()

    segments = [
        ReadingSegment(seq=r["seq"], english=r["english"], german=r["german"])
        for r in seg_rows
    ]
    return ChapterDetail(
        book_id=book["id"],
        book_title=book["title"],
        idx=idx,
        title=title,
        prev_idx=idx - 1 if idx > 0 else None,
        next_idx=idx + 1 if idx + 1 < count else None,
        segments=segments,
    )


def delete_book(book_id: int) -> bool:
    with _connect() as conn:
        conn.execute("DELETE FROM reading_chapters WHERE book_id = ?", (book_id,))
        conn.execute("DELETE FROM reading_segments WHERE book_id = ?", (book_id,))
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return cur.rowcount > 0


# --- flashcards --------------------------------------------------------------


def _row_to_flashcard(row: sqlite3.Row) -> Flashcard:
    return Flashcard(
        id=row["id"],
        book_id=row["book_id"],
        english=row["english"],
        german=row["german"],
        target_de=row["target_de"],
        target_en=row["target_en"],
        pos=row["pos"],
        lemma=row["lemma"],
        declension=CardDeclension(**json.loads(row["declension"])),
        note=row["note"],
        section_numbers=json.loads(row["section_numbers"]),
        due=row["due"],
        interval=row["interval"],
        ease=row["ease"],
        reps=row["reps"],
        lapses=row["lapses"],
        last_reviewed=row["last_reviewed"],
        created_at=row["created_at"],
    )


def create_flashcard(card: FlashcardData) -> Flashcard:
    """Persist a new card with a fresh SM-2 schedule (due immediately)."""
    sched = srs.initial()
    created = srs.now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO flashcards
                (book_id, english, german, target_de, target_en, pos, lemma,
                 declension, note, section_numbers,
                 due, interval, ease, reps, lapses, last_reviewed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card.book_id,
                card.english,
                card.german,
                card.target_de,
                card.target_en,
                card.pos,
                card.lemma,
                json.dumps(card.declension.model_dump()),
                card.note,
                json.dumps(card.section_numbers),
                sched.due,
                sched.interval,
                sched.ease,
                sched.reps,
                sched.lapses,
                None,
                created,
            ),
        )
        row = conn.execute("SELECT * FROM flashcards WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_flashcard(row)


def list_flashcards() -> list[Flashcard]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM flashcards ORDER BY created_at DESC").fetchall()
    return [_row_to_flashcard(r) for r in rows]


def list_due_flashcards(today_iso: str) -> list[Flashcard]:
    """Cards whose due date is on or before today, oldest-due first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flashcards WHERE due <= ? ORDER BY due, id",
            (today_iso,),
        ).fetchall()
    return [_row_to_flashcard(r) for r in rows]


def get_flashcard(card_id: int) -> Flashcard | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM flashcards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_flashcard(row) if row else None


def review_flashcard(card_id: int, rating: str) -> Flashcard | None:
    """Apply a review grade, persist the new SM-2 state, return the updated card."""
    card = get_flashcard(card_id)
    if not card:
        return None
    sched = srs.review(rating, card.interval, card.ease, card.reps, card.lapses)
    with _connect() as conn:
        conn.execute(
            """
            UPDATE flashcards
               SET due = ?, interval = ?, ease = ?, reps = ?, lapses = ?, last_reviewed = ?
             WHERE id = ?
            """,
            (sched.due, sched.interval, sched.ease, sched.reps, sched.lapses, srs.now_iso(), card_id),
        )
        row = conn.execute("SELECT * FROM flashcards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_flashcard(row)


def update_flashcard(card_id: int, card: FlashcardData) -> Flashcard | None:
    """Overwrite a card's editable content, leaving its SM-2 schedule untouched."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE flashcards
               SET book_id = ?, english = ?, german = ?, target_de = ?, target_en = ?,
                   pos = ?, lemma = ?, declension = ?, note = ?, section_numbers = ?
             WHERE id = ?
            """,
            (
                card.book_id,
                card.english,
                card.german,
                card.target_de,
                card.target_en,
                card.pos,
                card.lemma,
                json.dumps(card.declension.model_dump()),
                card.note,
                json.dumps(card.section_numbers),
                card_id,
            ),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM flashcards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_flashcard(row)


def delete_flashcard(card_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))
    return cur.rowcount > 0
