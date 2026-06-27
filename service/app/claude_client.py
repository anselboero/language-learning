"""Claude-backed grammar ingestion and lookup.

Hammer's German Grammar is far too long to extract in a single structured-output
pass, so theory and practice PDFs are sliced into fixed page windows with pypdf.
Each window is sent to Claude for structured extraction, and the results are
merged across windows by the book's decimal section number (which also bridges
sections that straddle a page boundary).
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Iterator
from io import BytesIO
from typing import Any

import anthropic
from pypdf import PdfReader, PdfWriter

from .models import (
    AlignedText,
    AskResponse,
    AssessmentResult,
    Chapter,
    Exercise,
    ExerciseData,
    ExtractedExercises,
    ExtractedGrammar,
    GrammarSection,
    GrammarSectionData,
    ReadingSegment,
    StoredChunk,
    SubmittedAnswer,
)

MODEL = "claude-opus-4-8"

# Pages per Claude call during ingestion. Smaller = more calls but bounded output.
PAGE_WINDOW = int(os.environ.get("INGEST_PAGE_WINDOW", "8"))

_client = anthropic.Anthropic()


# --- structured-output schema helper ----------------------------------------


def _strict_schema(model: type) -> dict[str, Any]:
    """Pydantic model -> structured-output JSON schema (objects forbid extra props)."""
    schema = model.model_json_schema()

    def harden(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
            for value in node.values():
                harden(value)
        elif isinstance(node, list):
            for item in node:
                harden(item)

    harden(schema)
    return schema


_GRAMMAR_SCHEMA = _strict_schema(ExtractedGrammar)
_EXERCISES_SCHEMA = _strict_schema(ExtractedExercises)


# --- hierarchy derivation ----------------------------------------------------


def _derive(number: str) -> tuple[int, str | None, int]:
    """From a decimal section number, derive (chapter_number, parent_number, level)."""
    segments = number.split(".")
    level = len(segments)
    try:
        chapter_number = int(segments[0])
    except ValueError:
        chapter_number = 0
    parent_number = ".".join(segments[:-1]) if level > 1 else None
    return chapter_number, parent_number, level


# --- PDF windowing -----------------------------------------------------------


def _pdf_windows(pdf_bytes: bytes) -> Iterator[bytes]:
    """Yield a small PDF for each consecutive window of PAGE_WINDOW pages."""
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = reader.pages
    for start in range(0, len(pages), PAGE_WINDOW):
        writer = PdfWriter()
        for page in pages[start : start + PAGE_WINDOW]:
            writer.add_page(page)
        buf = BytesIO()
        writer.write(buf)
        yield buf.getvalue()


def _document_block(pdf_bytes: bytes) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
        },
    }


def _extract_window(pdf_bytes: bytes, prompt: str, schema: dict[str, Any]) -> str:
    # Stream so we can use a large max_tokens without hitting the SDK's
    # non-streaming timeout guard; table-formatted output runs long.
    with _client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": [_document_block(pdf_bytes), {"type": "text", "text": prompt}]}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this document.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            "A page window exceeded the output limit even at 32k tokens. "
            "Lower INGEST_PAGE_WINDOW (e.g. to 4) and re-ingest."
        )
    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("No structured output returned from Claude.")
    return text


# --- theory ingestion --------------------------------------------------------

_THEORY_PROMPT = (
    "These are consecutive pages from Hammer's German Grammar and Usage. "
    "Extract: (1) any chapter headings that begin on these pages, and (2) every "
    "grammar section whose text appears on these pages, using the book's EXACT "
    "decimal section numbers as printed (e.g. '12.3.2'). A section may be cut off "
    "at a page edge — extract whatever is present; partial sections will be merged "
    "with the adjacent window. Do not invent section numbers.\n\n"
    "Format the `rule` field as clean, readable Markdown that mirrors the book's layout:\n"
    "- Explanatory sentences are ordinary paragraphs.\n"
    "- Preserve the book's **bold**: wrap in `**…**` every word the book prints in "
    "bold — e.g. the articles der/die/das, key forms, and headword nouns in examples.\n"
    "- Render every example set as a two-column Markdown table with headers "
    "`| German | English |`. Put ONE example per row. Use the book's English gloss; "
    "leave the English cell empty when the book gives none. Keep each example group "
    "as its own table, placed directly under the sentence that introduces it.\n"
    "- Preserve conjugation tables and grammatical paradigms as Markdown tables.\n"
    "- Whenever the text refers to another section by its decimal number, render it as "
    "a Markdown link to `/sections/<number>`, e.g. `[1.1.9](/sections/1.1.9)`. For a "
    "range like 1.1.1–1.1.4, link each endpoint separately: "
    "`[1.1.1](/sections/1.1.1)–[1.1.4](/sections/1.1.4)`.\n"
    "- Put any 'NB' note on its own line at the end, prefixed 'NB:'.\n"
    "Never collapse multiple examples onto one line or into a paragraph.\n\n"
    "Also fill `examples` with the plain German example sentences (no glosses) and "
    "`keywords` with the German words and grammatical terms the section governs — "
    "these are stored for search and flashcards but are not shown in the rule text."
)


def _merge_section(existing: GrammarSection, new: GrammarSectionData) -> None:
    """Fold a section seen again in a later window into the one we already have."""
    if new.rule.strip() and new.rule.strip() not in existing.rule:
        existing.rule = f"{existing.rule}\n\n{new.rule}".strip()
    if len(new.summary) > len(existing.summary):
        existing.summary = new.summary
    if not existing.title and new.title:
        existing.title = new.title
    for ex in new.examples:
        if ex not in existing.examples:
            existing.examples.append(ex)
    for kw in new.keywords:
        if kw not in existing.keywords:
            existing.keywords.append(kw)
    for ref in new.cross_references:
        if ref not in existing.cross_references:
            existing.cross_references.append(ref)


def ingest_theory_pdf(pdf_bytes: bytes) -> dict[str, int]:
    """Window the theory PDF through Claude and return chapters + merged sections."""
    chapters: dict[int, Chapter] = {}
    sections: dict[str, GrammarSection] = {}

    for window in _pdf_windows(pdf_bytes):
        extracted = ExtractedGrammar.model_validate_json(
            _extract_window(window, _THEORY_PROMPT, _GRAMMAR_SCHEMA)
        )
        for chapter in extracted.chapters:
            chapters.setdefault(chapter.number, chapter)
        for data in extracted.sections:
            if not data.number.strip():
                continue
            if data.number in sections:
                _merge_section(sections[data.number], data)
            else:
                chapter_number, parent_number, level = _derive(data.number)
                sections[data.number] = GrammarSection(
                    **data.model_dump(),
                    chapter_number=chapter_number,
                    parent_number=parent_number,
                    level=level,
                )

    from . import db

    return db.replace_theory(list(chapters.values()), list(sections.values()))


# --- practice ingestion ------------------------------------------------------

_PRACTICE_PROMPT = (
    "These are consecutive pages from Practising German Grammar, the workbook "
    "cross-referenced to Hammer's German Grammar and Usage (GGU). Extract every "
    "exercise on these pages as a single unit — do NOT split an exercise into one "
    "entry per item. For each exercise capture:\n"
    "- chapter_number: the workbook chapter number (matches the GGU chapter), e.g. 1;\n"
    "- label: the exercise number as printed, e.g. '5';\n"
    "- title: the exercise title, e.g. 'Gender';\n"
    "- instructions: what the learner must do;\n"
    "- section_refs: the GGU section references printed under the title, as a list of "
    "BARE section numbers only (digits and dots). Keep ranges as 'A–B'. Do NOT include "
    "the '§' symbol, the words 'Section(s)'/'GGU', or any 'Table'/'Figure' reference. "
    "Convert an 'f'/'ff' suffix to the base number (write '1.1.2' for '1.1.2f'). If the "
    "header says e.g. '1.1.1 and Table 1.2', include only ['1.1.1']. Examples: "
    "['1.1.1–1.1.9'] or ['12.3', '12.4'];\n"
    "- items: the individual numbered items in order, each with its prompt and (if an "
    "answer key is present) its answer.\n"
    "An exercise may be cut off at a page edge; extract what is present — units with "
    "the same label and chapter will be merged with the adjacent window."
)


_SECTION_TOKEN = re.compile(r"^\d+(?:\.\d+)*$")


def _normalize_token(token: str) -> str | None:
    """A single section number, cleaned. Returns None if it isn't one.

    Strips the '§' symbol and the 'f'/'ff' ('and following') suffix; rejects
    anything that isn't pure digits-and-dots (e.g. 'Table 1.2', 'Figure 3').
    """
    token = token.replace("§", "").strip()
    token = re.sub(r"f{1,2}$", "", token).strip()  # 1.1.2f -> 1.1.2
    return token if _SECTION_TOKEN.match(token) else None


def _clean_section_refs(refs: list[str]) -> list[str]:
    """Keep only valid GGU section numbers/ranges; drop table/figure refs etc."""
    out: list[str] = []
    for ref in refs:
        matched_range = False
        for dash in ("–", "—", "-"):
            if dash in ref:
                start, _, end = ref.partition(dash)
                a, b = _normalize_token(start), _normalize_token(end)
                if a and b:
                    out.append(f"{a}–{b}")
                elif a or b:
                    out.append(a or b)  # type: ignore[arg-type]
                matched_range = True
                break
        if matched_range:
            continue
        single = _normalize_token(ref)
        if single:
            out.append(single)
        else:
            # Recover a leading section number from a lumped ref like
            # "1.1.1 and Table 1.2" (a bare "Table 1.2" still yields nothing).
            lead = re.match(r"\s*§?\s*(\d+(?:\.\d+)*)", ref)
            if lead:
                out.append(lead.group(1))

    seen: set[str] = set()
    deduped: list[str] = []
    for ref in out:
        if ref not in seen:
            seen.add(ref)
            deduped.append(ref)
    return deduped


def ingest_practice_pdf(pdf_bytes: bytes) -> int:
    """Window the practice PDF through Claude and store merged exercise units."""
    merged: dict[tuple[int, str], ExerciseData] = {}

    for window in _pdf_windows(pdf_bytes):
        extracted = ExtractedExercises.model_validate_json(
            _extract_window(window, _PRACTICE_PROMPT, _EXERCISES_SCHEMA)
        )
        for ex in extracted.exercises:
            key = (ex.chapter_number, ex.label)
            existing = merged.get(key)
            if existing is None:
                merged[key] = ex
                continue
            # Same exercise split across a page boundary — fold it in.
            seen = {i.prompt for i in existing.items}
            for item in ex.items:
                if item.prompt not in seen:
                    existing.items.append(item)
                    seen.add(item.prompt)
            for ref in ex.section_refs:
                if ref not in existing.section_refs:
                    existing.section_refs.append(ref)
            if len(ex.instructions) > len(existing.instructions):
                existing.instructions = ex.instructions
            if not existing.title and ex.title:
                existing.title = ex.title

    exercises = list(merged.values())
    for ex in exercises:
        ex.section_refs = _clean_section_refs(ex.section_refs)

    from . import db

    return db.replace_practice(exercises)


# --- lookup ------------------------------------------------------------------

_ASK_SCHEMA = _strict_schema(AskResponse)


def ask(query: str, sections: list[GrammarSection]) -> AskResponse:
    """Answer a word/free-text grammar question grounded in the stored sections."""
    if not sections:
        return AskResponse(
            answer="No grammar has been ingested yet. Upload the theory PDF first.",
            section_numbers=[],
        )

    catalogue = "\n\n".join(
        f"[{s.number}] {s.title}\n"
        f"Summary: {s.summary}\n"
        f"Keywords: {', '.join(s.keywords) or '—'}\n"
        f"Rule: {s.rule}"
        for s in sections
    )

    system = (
        "You are a German grammar tutor. The user gives you a word, phrase, or question. "
        "Using ONLY the grammar sections provided (from Hammer's German Grammar and Usage), "
        "identify which section(s) are relevant and explain the rule that applies, in clear "
        "plain language. Cite the decimal section numbers you relied on. If nothing in the "
        "provided sections is relevant, say so honestly and return an empty section_numbers list."
    )

    response = _client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _ASK_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": f"Available grammar sections:\n\n{catalogue}\n\n---\n\nUser question: {query}",
            }
        ],
    )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("No answer returned from Claude.")
    return AskResponse.model_validate(json.loads(text))


# --- assessing exercise answers ----------------------------------------------

_ASSESS_SCHEMA = _strict_schema(AssessmentResult)


def assess(
    exercise: Exercise,
    answers: list[SubmittedAnswer],
    sections: list[GrammarSection],
) -> AssessmentResult:
    """Grade a learner's answers to one exercise, grounded in the covered sections."""

    by_index = {a.index: a.answer for a in answers}
    items_block = "\n".join(
        f"[{i}] {item.prompt}"
        + (f"\n    answer key: {item.answer}" if item.answer else "")
        + f"\n    learner's answer: {by_index.get(i, '(left blank)')}"
        for i, item in enumerate(exercise.items)
    )

    catalogue = (
        "\n\n".join(
            f"[{s.number}] {s.title}\nSummary: {s.summary}\nRule: {s.rule}"
            for s in sections
        )
        or "(no grammar sections available for this exercise's range)"
    )

    system = (
        "You are a German grammar tutor grading a learner's exercise answers. For each "
        "item decide if the learner's answer is correct (accept trivial spacing/case "
        "differences; be strict about grammar — articles, endings, forms). Give the "
        "correct answer and, when wrong, a one-line explanation of the mistake. Cite the "
        "GGU section number(s) that explain the relevant rule, choosing ONLY from the "
        "sections provided. When an item has an answer key, treat it as ground truth. "
        "Finish with a short overall summary and a list of sections worth reviewing based "
        "on the mistakes. Reply in English."
    )

    response = _client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _ASSESS_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Exercise {exercise.label} — {exercise.title}\n"
                    f"Instructions: {exercise.instructions}\n\n"
                    f"Items and answers:\n{items_block}\n\n"
                    f"Relevant grammar sections:\n{catalogue}"
                ),
            }
        ],
    )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("No assessment returned from Claude.")
    return AssessmentResult.model_validate(json.loads(text))


# --- reading: diglot-weave alignment -----------------------------------------

_ALIGN_SCHEMA = _strict_schema(AlignedText)

_ALIGN_PROMPT = (
    "You are given the SAME book in two languages: an English text and its German "
    "translation. Align them into sentence-level segments in reading order, so that "
    "each segment pairs one English sentence (or a couple of short ones that must stay "
    "together) with the German sentence(s) that translate it.\n\n"
    "For every segment return:\n"
    "- `german`: the matching German sentence(s), verbatim from the German text.\n"
    "- `chunks`: the ENGLISH sentence split into an ordered list of chunks. "
    "Concatenating every chunk's `text` in order MUST reproduce the English sentence "
    "exactly, including all spaces and punctuation.\n\n"
    "A chunk is either plain text or a weaveable word:\n"
    "- Plain chunks carry the connective text, punctuation, and whitespace; leave their "
    "`de` and `gloss` null.\n"
    "- Weaveable chunks are content words a learner should acquire — nouns, main verbs, "
    "adjectives, and common adverbs. For each, set `de` to the contextually-correct "
    "German form and `gloss` to a 1–3 word English meaning.\n"
    "  • For a noun, GROUP it with its preceding article/determiner into one chunk "
    "(`text` = 'the wolf') and give the full German noun phrase with the correct article "
    "and capitalization in `de` (`de` = 'der Wolf').\n"
    "  • For a verb or adjective, weave just the word, inflected to match the German "
    "translation as it actually appears.\n"
    "- Do NOT weave proper names, numbers, or pure function words on their own.\n\n"
    "Keep the original English wording — never paraphrase. If a German sentence has no "
    "English counterpart (or vice versa), attach it to the nearest segment rather than "
    "inventing text."
)


def _stream_text(content: list[dict[str, Any]], schema: dict[str, Any]) -> str:
    """One streamed structured-output call returning the JSON text block."""
    with _client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this text.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            "The book is too long to align in one pass (hit the 32k output limit). "
            "Try a shorter text for now."
        )
    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("No alignment returned from Claude.")
    return text


def _lemma_key(chunk: Any) -> str:
    """Stable grouping key for a weaveable chunk: its meaning, lowercased."""
    return (chunk.gloss or chunk.text).strip().lower()


def _rank_segments(aligned: AlignedText) -> tuple[list[ReadingSegment], int]:
    """Assign each weaveable chunk a global frequency rank (0 = most frequent).

    Words that recur most across the book get the lowest ranks, so they are woven
    in first as the density rises — maximizing exposure to high-frequency vocabulary.
    """
    counts: dict[str, int] = {}
    for seg in aligned.segments:
        for chunk in seg.chunks:
            if chunk.de:
                counts[_lemma_key(chunk)] = counts.get(_lemma_key(chunk), 0) + 1

    ordered = sorted(counts, key=lambda k: (-counts[k], k))
    rank_of = {key: i for i, key in enumerate(ordered)}

    segments: list[ReadingSegment] = []
    for i, seg in enumerate(aligned.segments):
        stored = [
            StoredChunk(
                text=chunk.text,
                de=chunk.de,
                gloss=chunk.gloss,
                rank=rank_of[_lemma_key(chunk)] if chunk.de else None,
            )
            for chunk in seg.chunks
        ]
        english = "".join(chunk.text for chunk in seg.chunks)
        segments.append(ReadingSegment(seq=i, english=english, german=seg.german, chunks=stored))
    return segments, len(ordered)


def ingest_book(title: str, author: str, english_text: str, german_text: str) -> int:
    """Align an English/German text pair into a stored diglot-weave book."""
    content = [
        {"type": "text", "text": _ALIGN_PROMPT},
        {"type": "text", "text": f"=== ENGLISH TEXT ===\n\n{english_text}"},
        {"type": "text", "text": f"=== GERMAN TEXT ===\n\n{german_text}"},
    ]
    aligned = AlignedText.model_validate_json(_stream_text(content, _ALIGN_SCHEMA))
    if not aligned.segments:
        raise RuntimeError("No aligned segments were produced from these texts.")

    segments, vocab_size = _rank_segments(aligned)

    from . import db

    return db.create_book(title, author, vocab_size, segments)
