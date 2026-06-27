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

from .models import (
    Chapter,
    ChapterWithSections,
    Exercise,
    ExerciseData,
    ExerciseItem,
    GrammarSection,
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

            CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(chapter_number);
            CREATE INDEX IF NOT EXISTS idx_exercises_chapter ON exercises(chapter_number);
            """
        )


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


def replace_theory(chapters: list[Chapter], sections: list[GrammarSection]) -> dict[str, int]:
    """Replace all theory content (chapters + sections) with a fresh ingestion."""
    with _connect() as conn:
        conn.execute("DELETE FROM chapters")
        conn.execute("DELETE FROM sections")
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


def replace_practice(exercises: list[ExerciseData]) -> int:
    with _connect() as conn:
        conn.execute("DELETE FROM exercises")
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
