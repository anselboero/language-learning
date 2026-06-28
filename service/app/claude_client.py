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
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any

import anthropic
from pydantic import ValidationError
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
    SelectionTranslation,
    StoredChunk,
    SubmittedAnswer,
)

MODEL = "claude-opus-4-8"

# Reading ingestion (translate + align a whole book) is output-heavy and the
# dominant cost driver, but it's a mechanical transform that a cheaper model
# handles well — so it's configurable and defaults to Sonnet. The grammar
# features (ask/assess/theory extraction) stay on the Opus MODEL above.
READING_MODEL = os.environ.get("READING_MODEL", "claude-sonnet-4-6")

# A whole book won't fit in one structured-output pass (the weave JSON is far
# larger than the source text and hits the 32k output cap), so German-only
# ingestion is split into chunks of roughly this many characters. Each chunk is
# translated+aligned independently, then all segments are merged and ranked
# globally. A chunk that still overflows is split in half and retried.
READING_CHUNK_CHARS = int(os.environ.get("READING_CHUNK_CHARS", "6000"))

# Chunks are independent, so they're aligned concurrently to cut wall-clock on
# long books. Kept modest to stay within the model's rate limits.
READING_CONCURRENCY = int(os.environ.get("READING_CONCURRENCY", "6"))

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


def _catalogue(sections: list[GrammarSection]) -> str:
    """Render the stored sections as the grounding catalogue passed to Claude."""
    return "\n\n".join(
        f"[{s.number}] {s.title}\n"
        f"Summary: {s.summary}\n"
        f"Keywords: {', '.join(s.keywords) or '—'}\n"
        f"Rule: {s.rule}"
        for s in sections
    )


def _ask_response(text: str) -> AskResponse:
    return AskResponse.model_validate(json.loads(text))


def ask(query: str, sections: list[GrammarSection]) -> AskResponse:
    """Answer a word/free-text grammar question grounded in the stored sections."""
    if not sections:
        return AskResponse(
            answer="No grammar has been ingested yet. Upload the theory PDF first.",
            section_numbers=[],
        )

    catalogue = _catalogue(sections)

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
    return _ask_response(text)


# --- selection: translate / grammar context / free question ------------------
#
# Three actions a learner can take on a span they highlighted while reading. The
# selected text plus its enclosing German sentence are passed so the model
# disambiguates the same word across contexts.

_TRANSLATE_SELECTION_SCHEMA = _strict_schema(SelectionTranslation)


def _context_block(context: str | None) -> str:
    return f"\n\nIt appears in this sentence:\n{context}" if context else ""


def translate_selection(text: str, context: str | None = None) -> SelectionTranslation:
    """Translate a highlighted German span to English (cheap reading model)."""
    system = (
        "You are a German→English translator for someone reading a German book. "
        "Translate the given German text into natural, faithful English. If a short "
        "usage note would genuinely help the learner (an idiom, a separable verb, a "
        "case-governed meaning, or register), add ONE brief note; otherwise leave it "
        "empty. Do not pad the translation with commentary."
    )
    response = _client.messages.create(
        model=READING_MODEL,
        max_tokens=800,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _TRANSLATE_SELECTION_SCHEMA}},
        messages=[{"role": "user", "content": f"German text: {text}{_context_block(context)}"}],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if not out:
        raise RuntimeError("No translation returned from Claude.")
    return SelectionTranslation.model_validate(json.loads(out))


def explain_grammar(
    text: str, context: str | None, sections: list[GrammarSection]
) -> AskResponse:
    """Explain the grammar of a selection, grounded strictly in stored sections."""
    if not sections:
        return AskResponse(
            answer="No grammar has been ingested yet. Upload the theory PDF first.",
            section_numbers=[],
        )

    system = (
        "You are a German grammar tutor. The learner highlighted a word or phrase while "
        "reading and wants to understand the grammar at work in it — cases, endings, word "
        "order, verb forms, articles. Using ONLY the grammar sections provided (from "
        "Hammer's German Grammar and Usage), explain the rule(s) that apply in clear plain "
        "language and cite the decimal section numbers you relied on. If nothing in the "
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
                "content": (
                    f"Available grammar sections:\n\n{_catalogue(sections)}\n\n---\n\n"
                    f"Selected text: {text}{_context_block(context)}"
                ),
            }
        ],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if not out:
        raise RuntimeError("No explanation returned from Claude.")
    return _ask_response(out)


def ask_free(
    text: str, question: str, context: str | None, sections: list[GrammarSection]
) -> AskResponse:
    """Answer a free-form question about a selection; cite sections when relevant."""
    catalogue = _catalogue(sections) if sections else "(no grammar sections available)"
    system = (
        "You are a helpful German tutor for someone reading a German book. Answer the "
        "learner's question about the highlighted text clearly and accurately. Grammar "
        "sections from Hammer's German Grammar and Usage are provided: when your answer "
        "relies on one, cite its decimal section number; but you are NOT limited to them — "
        "answer the question fully even when no section applies, leaving section_numbers "
        "empty in that case. Be concise and concrete."
    )
    response = _client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _ASK_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Available grammar sections:\n\n{catalogue}\n\n---\n\n"
                    f"Selected text: {text}{_context_block(context)}\n\n"
                    f"Learner's question: {question}"
                ),
            }
        ],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if not out:
        raise RuntimeError("No answer returned from Claude.")
    return _ask_response(out)


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
    "- Be THOROUGH: mark EVERY content word as weaveable, not just a representative few. "
    "Aim to cover essentially all of them — every noun, every main and auxiliary verb, every "
    "adjective and adverb, and meaningful pronouns and numbers. When a word could plausibly be "
    "learned, make it weaveable. Only genuine function words (standalone articles, conjunctions, "
    "prepositions, particles) and punctuation stay as plain chunks. For each weaveable chunk, set "
    "`de` to the contextually-correct German form and `gloss` to a 1–3 word English meaning.\n"
    "  • For a noun, GROUP it with its preceding article/determiner into one chunk "
    "(`text` = 'the wolf') and give the full German noun phrase with the correct article "
    "and capitalization in `de` (`de` = 'der Wolf').\n"
    "  • For a verb or adjective, weave just the word, inflected to match the German "
    "translation as it actually appears.\n"
    "  • Group a MULTI-WORD UNIT into one weaveable chunk when the German is a fixed or "
    "idiomatic expression that does NOT map word-for-word to the English — e.g. genitive/time "
    "phrases ('one morning' → 'eines Morgens'), set phrases ('for example' → 'zum Beispiel'), "
    "or a separable verb with its particle. Set `text` to the whole English span, `de` to the "
    "whole German phrase, and `gloss` to its meaning. Default to single words; only group when "
    "word-by-word substitution would read unnaturally or split a fixed expression.\n"
    "- Do NOT weave proper names or pure function words on their own.\n\n"
    "Keep the original English wording — never paraphrase. If a German sentence has no "
    "English counterpart (or vice versa), attach it to the nearest segment rather than "
    "inventing text."
)


_TRANSLATE_PROMPT = (
    "You are given a book in German. Produce an English diglot-weave scaffold for it.\n\n"
    "Work through the German text in reading order, one segment at a time (a sentence, or "
    "a couple of short sentences that must stay together). For every segment return:\n"
    "- `german`: the German sentence(s), VERBATIM from the source — never reword the German.\n"
    "- `chunks`: a faithful English translation of that segment, split into an ordered list "
    "of chunks. Aim for natural but LITERAL English — one English sentence per German "
    "sentence, minimal reordering — so the two line up closely. Concatenating every chunk's "
    "`text` in order MUST reproduce your English sentence exactly, including spaces and "
    "punctuation.\n\n"
    "A chunk is either plain text or a weaveable word:\n"
    "- Plain chunks carry connective words, punctuation, and whitespace; leave `de`/`gloss` null.\n"
    "- Be THOROUGH: mark EVERY content word as weaveable, not just a representative few. "
    "Aim to cover essentially all of them — every noun, every main and auxiliary verb, every "
    "adjective and adverb, and meaningful pronouns and numbers. When a word could plausibly be "
    "learned, make it weaveable. Only genuine function words (standalone articles, conjunctions, "
    "prepositions, particles) and punctuation stay as plain chunks. For each weaveable chunk, set "
    "`de` to the ACTUAL German word from this segment's original sentence (not a re-translation) "
    "and `gloss` to a 1–3 word English meaning.\n"
    "  • For a noun, group it with its preceding English article into one chunk "
    "(`text` = 'the wolf') and give the German noun phrase with its real article and "
    "capitalization in `de` (`de` = 'der Wolf'), inflected as it appears in the German.\n"
    "  • For a verb or adjective, weave just the word, matching the German form actually used.\n"
    "  • Group a MULTI-WORD UNIT into one weaveable chunk when the German is a fixed or "
    "idiomatic expression that does NOT map word-for-word to the English — e.g. genitive/time "
    "phrases ('one morning' → 'eines Morgens'), set phrases ('for example' → 'zum Beispiel'), "
    "or a separable verb with its particle. Set `text` to the whole English span, `de` to the "
    "whole German phrase, and `gloss` to its meaning. Default to single words; only group when "
    "word-by-word substitution would read unnaturally or split a fixed expression.\n"
    "- Do NOT weave proper names or pure function words on their own.\n\n"
    "Every woven German word must be a real word from the original German segment, so the "
    "learner only ever sees the author's own German."
)


class _OutputLimitError(RuntimeError):
    """Raised when a single ingestion call exhausts the output token budget."""


def _stream_text(content: list[dict[str, Any]], schema: dict[str, Any]) -> str:
    """One streamed structured-output call returning the JSON text block."""
    with _client.messages.stream(
        model=READING_MODEL,
        max_tokens=32000,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this text.")
    if message.stop_reason == "max_tokens":
        raise _OutputLimitError("output limit reached")
    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("No alignment returned from Claude.")
    return text


_LEMMA_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _lemma_key(chunk: Any) -> str:
    """Stable grouping key for a weaveable chunk: its meaning, lowercased.

    Strips a leading English article so 'grandmother' and 'the grandmother' count
    as one word for frequency ranking, regardless of how the gloss was phrased.
    """
    key = (chunk.gloss or chunk.text).strip().lower()
    return _LEMMA_ARTICLE.sub("", key)


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


# --- text chunking for long books -------------------------------------------

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _pack_chunks(text: str, budget: int) -> list[str]:
    """Greedily group paragraphs into chunks of about `budget` characters."""
    units = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > budget:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks or ([text.strip()] if text.strip() else [])


def _halve(text: str) -> list[str]:
    """Split a too-big chunk near its midpoint, preferring a clean boundary."""
    for splitter in (_PARAGRAPH_SPLIT, _SENTENCE_SPLIT):
        parts = splitter.split(text)
        if len(parts) > 1:
            joiner = "\n\n" if splitter is _PARAGRAPH_SPLIT else " "
            # Split where the running character count first crosses the midpoint,
            # so the two halves are balanced by length rather than by part count.
            target, acc, mid = len(text) / 2, 0, 1
            for i, part in enumerate(parts):
                acc += len(part)
                if acc >= target:
                    mid = max(1, i)
                    break
            left = joiner.join(parts[:mid]).strip()
            right = joiner.join(parts[mid:]).strip()
            if left and right:
                return [left, right]
    midpoint = len(text) // 2
    return [text[:midpoint].strip(), text[midpoint:].strip()]


# Structured output is almost always valid JSON, but the model very occasionally
# emits a stray trailing comma; repair that before giving up, and otherwise
# regenerate the chunk a couple of times rather than failing the whole book.
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_PARSE_RETRIES = 2


def _parse_aligned(raw: str) -> AlignedText:
    """Parse alignment JSON, repairing a stray trailing comma if present."""
    try:
        return AlignedText.model_validate_json(raw)
    except ValidationError:
        return AlignedText.model_validate_json(_TRAILING_COMMA.sub(r"\1", raw))


def _translate_chunk(german: str, attempt: int = 0) -> list[AlignedSegmentData]:
    """Translate+align one chunk; split on overflow, regenerate on bad JSON."""
    content = [
        {"type": "text", "text": _TRANSLATE_PROMPT},
        {"type": "text", "text": f"=== GERMAN TEXT ===\n\n{german}"},
    ]
    try:
        raw = _stream_text(content, _ALIGN_SCHEMA)
    except _OutputLimitError:
        halves = _halve(german)
        if len(halves) < 2 or not all(halves):
            raise RuntimeError(
                "A single paragraph is too large to align even after splitting. "
                "Lower READING_CHUNK_CHARS and re-ingest."
            )
        out: list[AlignedSegmentData] = []
        for half in halves:
            out.extend(_translate_chunk(half))
        return out

    try:
        return _parse_aligned(raw).segments
    except ValidationError:
        if attempt < _PARSE_RETRIES:
            return _translate_chunk(german, attempt + 1)
        raise RuntimeError(
            "Claude returned malformed alignment JSON for a chunk after retries."
        )


def ingest_book(
    title: str,
    author: str,
    german_text: str,
    english_text: str | None = None,
) -> int:
    """Ingest a German text into a stored diglot-weave book.

    With only the German text, Claude generates a faithful, sentence-aligned English
    scaffold (the recommended path — the woven words stay the author's own German, and
    we avoid a published translation's literary license breaking the alignment). The
    German is split into character-bounded chunks so a full-length book fits, then all
    segments are merged and ranked globally. If a faithful English translation is
    supplied, the two are aligned directly in one pass instead.
    """
    if english_text:
        content = [
            {"type": "text", "text": _ALIGN_PROMPT},
            {"type": "text", "text": f"=== ENGLISH TEXT ===\n\n{english_text}"},
            {"type": "text", "text": f"=== GERMAN TEXT ===\n\n{german_text}"},
        ]
        try:
            raw = _stream_text(content, _ALIGN_SCHEMA)
        except _OutputLimitError as exc:
            raise RuntimeError(
                "This English+German pair is too long to align in one pass. Upload the "
                "German text on its own to use chunked ingestion."
            ) from exc
        segments = _parse_aligned(raw).segments
    else:
        chunks = _pack_chunks(german_text, READING_CHUNK_CHARS)
        if not chunks:
            raise RuntimeError("The text appears to be empty.")
        # Chunks are independent; align them concurrently. ThreadPoolExecutor.map
        # preserves input order, so the merged segments stay in reading order.
        with ThreadPoolExecutor(max_workers=min(READING_CONCURRENCY, len(chunks))) as pool:
            parts = list(pool.map(_translate_chunk, chunks))
        segments = [seg for part in parts for seg in part]

    if not segments:
        raise RuntimeError("No aligned segments were produced from these texts.")

    merged = AlignedText(segments=segments)
    stored_segments, vocab_size = _rank_segments(merged)

    from . import db

    return db.create_book(title, author, vocab_size, stored_segments)
