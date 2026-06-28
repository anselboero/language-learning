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
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any

import anthropic
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from . import dictionary
from .models import (
    AlignedText,
    AnswerCheck,
    AskResponse,
    AssessmentResult,
    CardSuggestion,
    Chapter,
    DetectedChapters,
    Exercise,
    ExerciseData,
    ExtractedExercises,
    ExtractedGrammar,
    GrammarSection,
    GrammarSectionData,
    ReadingChapter,
    ReadingSegment,
    SectionSelection,
    SelectionTranslation,
    SubmittedAnswer,
)

MODEL = "claude-opus-4-8"

# Reading ingestion (translate + align a whole book) is output-heavy and the
# dominant cost driver, but it's a mechanical transform that a cheaper model
# handles well — so it's configurable and defaults to Sonnet. The grammar
# features (ask/assess/theory extraction) stay on the Opus MODEL above.
READING_MODEL = os.environ.get("READING_MODEL", "claude-sonnet-4-6")

# A whole book won't fit in one structured-output pass (German + English roughly
# doubles the source and would hit the 32k output cap), so German-only ingestion
# is split into chunks of roughly this many characters. Each chunk is
# translated+aligned independently, then all segments are merged in reading order.
# A chunk that still overflows is split in half and retried.
READING_CHUNK_CHARS = int(os.environ.get("READING_CHUNK_CHARS", "12000"))

# Chunks are independent, so they're aligned concurrently to cut wall-clock on
# long books. Kept modest to stay within the model's rate limits.
READING_CONCURRENCY = int(os.environ.get("READING_CONCURRENCY", "6"))

# Pages per Claude call during ingestion. Smaller = more calls but bounded output.
PAGE_WINDOW = int(os.environ.get("INGEST_PAGE_WINDOW", "8"))

# The API can return transient 429/5xx/529 ("overloaded") errors, especially on a
# long ingestion that fires many calls. The SDK retries these automatically on
# plain create() calls; we raise max_retries above the default 2 so a burst of
# overload doesn't sink a whole ingestion. Streaming calls need extra handling
# (see _with_retries) because an overload surfacing mid-stream isn't auto-retried.
_client = anthropic.Anthropic(max_retries=6)


# Transient conditions worth retrying with exponential backoff.
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(
        exc, (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)
    ):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS
    return "overloaded" in str(exc).lower()


def _with_retries(fn: Callable[[], Any], *, attempts: int = 6, base_delay: float = 2.0) -> Any:
    """Run a streaming Claude call, retrying transient overload/rate errors.

    The SDK's built-in retry covers non-streaming requests but not an error event
    that arrives partway through a stream, so the ingestion's streamed calls wrap
    their body in this. Terminal errors (refusal, output-limit) aren't retryable
    and propagate immediately.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts - 1 or not _is_retryable(exc):
                raise
            time.sleep(min(base_delay * 2**attempt, 30.0))


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
    def _call() -> str:
        with _client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[
                {"role": "user", "content": [_document_block(pdf_bytes), {"type": "text", "text": prompt}]}
            ],
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

    return _with_retries(_call)


# --- theory ingestion --------------------------------------------------------

_THEORY_PROMPT = (
    "These are consecutive pages from Hammer's German Grammar and Usage. "
    "Extract: (1) any chapter headings that begin on these pages, and (2) every "
    "grammar section whose text appears on these pages, using the book's EXACT "
    "decimal section numbers as printed (e.g. '12.3.2'). A section may be cut off "
    "at a page edge — extract whatever is present; partial sections will be merged "
    "with the adjacent window.\n\n"
    "When a chapter OPENS with introductory material before its first numbered "
    "subsection — the opening prose, a 'this chapter covers…' overview, or an "
    "introductory table (e.g. Table 4.1) — capture it as ONE section whose `number` "
    "is the bare chapter number (e.g. '4' for 'Chapter 4 The articles') and whose "
    "`title` is the chapter's own title. Always emit this chapter-intro section when "
    "such material is present, so a chapter's opening is never dropped. Apart from "
    "this, do not invent section numbers — every other section must use a decimal "
    "number printed in the book.\n\n"
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

    return db.upsert_theory(list(chapters.values()), list(sections.values()))


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

    return db.upsert_practice(exercises)


# --- lookup ------------------------------------------------------------------

_ASK_SCHEMA = _strict_schema(AskResponse)


def _catalogue(sections: list[GrammarSection]) -> str:
    """Render the FULL text of the given sections — used for the few that are relevant."""
    return "\n\n".join(
        f"[{s.number}] {s.title}\n"
        f"Summary: {s.summary}\n"
        f"Keywords: {', '.join(s.keywords) or '—'}\n"
        f"Rule: {s.rule}"
        for s in sections
    )


def _index(sections: list[GrammarSection]) -> str:
    """A lightweight catalogue — number, title, summary, keywords, but NO rule text.

    Small enough to send on every call so Claude can see what exists and pick the
    relevant sections, rather than receiving the whole book's rules each time.
    """
    return "\n".join(
        f"[{s.number}] {s.title} — {s.summary} (keywords: {', '.join(s.keywords) or '—'})"
        for s in sections
    )


_SECTION_SELECT_SCHEMA = _strict_schema(SectionSelection)


def _select_relevant(
    query: str, sections: list[GrammarSection], *, limit: int = 8
) -> list[GrammarSection]:
    """Pick the sections relevant to a query from the index — one cheap Sonnet call.

    Returns the matching sections (with full text), most relevant first, capped at
    ``limit``. Empty when nothing is clearly relevant.
    """
    if not sections:
        return []
    by_number = {s.number: s for s in sections}
    system = (
        "You are routing a German grammar question to the right reference sections. From the "
        "catalogue of Hammer's German Grammar and Usage sections below (number, title, summary, "
        "keywords), pick the few whose rules are most relevant to the user's text — usually 1–5, "
        f"at most {limit}, most relevant first. Use ONLY numbers that appear in the catalogue; "
        "return an empty list if none are clearly relevant."
    )
    response = _client.messages.create(
        model=READING_MODEL,
        max_tokens=300,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _SECTION_SELECT_SCHEMA}},
        messages=[
            {"role": "user", "content": f"Catalogue:\n\n{_index(sections)}\n\n---\n\nText: {query}"}
        ],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if not out:
        return []
    selection = SectionSelection.model_validate(json.loads(out))
    # Preserve the model's relevance order, skip unknown numbers, cap the count.
    chosen: list[GrammarSection] = []
    for number in selection.numbers:
        section = by_number.get(number)
        if section and section not in chosen:
            chosen.append(section)
        if len(chosen) >= limit:
            break
    return chosen


def _ask_response(text: str) -> AskResponse:
    return AskResponse.model_validate(json.loads(text))


def ask(query: str, sections: list[GrammarSection]) -> AskResponse:
    """Answer a word/free-text grammar question grounded in the stored sections."""
    if not sections:
        return AskResponse(
            answer="No grammar has been ingested yet. Upload the theory PDF first.",
            section_numbers=[],
        )

    relevant = _select_relevant(query, sections)
    catalogue = _catalogue(relevant) if relevant else "(no clearly relevant sections found)"

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

    relevant = _select_relevant(f"{text}{_context_block(context)}", sections)
    catalogue = _catalogue(relevant) if relevant else "(no clearly relevant sections found)"

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
                    f"Available grammar sections:\n\n{catalogue}\n\n---\n\n"
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
    relevant = _select_relevant(f"{question}\n{text}{_context_block(context)}", sections)
    catalogue = _catalogue(relevant) if relevant else "(no grammar sections available)"
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


# --- flashcards --------------------------------------------------------------
#
# A card is proposed from a reading selection: the model translates the sentence,
# locates the English word that renders the highlighted German one, classifies it,
# and offers a short Context Note only when a real grammar point applies. The
# inflection it returns is then overridden, where possible, by the deterministic
# Wiktionary dictionary so gender / plural / principal parts never rely on a guess.

_CARD_SCHEMA = _strict_schema(CardSuggestion)


# Wiktionary reports the perfect auxiliary as an infinitive ('sein'/'haben');
# the card wants its 3rd-person-singular form, as it reads in 'ist gelaufen'.
_AUX_3SG = {"sein": "ist", "haben": "hat"}


def _merge_dictionary_facts(card: CardSuggestion) -> CardSuggestion:
    """Override the suggested declension with sourced dictionary facts when available.

    The dictionary is keyed by the *canonical* form, so we look up the lemma the
    model proposed (a verb's infinitive, a noun without its article) rather than
    the inflected surface word — 'erwachte' has no Wiktionary entry, 'erwachen'
    does. Whatever it returns then corrects the model, including a wrong lemma.
    """
    entry = next((e for e in (dictionary.lookup(card.lemma), dictionary.lookup(card.target_de)) if e), None)
    if entry is None:
        return card

    forms = {f.label: f.form for f in entry.forms}

    if card.pos == "noun" and entry.part_of_speech == "noun":
        if entry.gender:
            card.declension.gender = entry.gender
            # Keep the lemma article in sync with the sourced gender.
            card.lemma = f"{entry.gender} {entry.word}"
        if forms.get("plural"):
            card.declension.plural = forms["plural"]
    elif card.pos == "verb" and entry.part_of_speech == "verb":
        card.declension.infinitive = entry.word
        card.lemma = entry.word
        if forms.get("preterite"):
            card.declension.preterite = forms["preterite"]
        participle = forms.get("past participle")
        if participle:
            auxiliary = _AUX_3SG.get((forms.get("auxiliary") or "haben").lower(), "hat")
            card.declension.perfect = f"{auxiliary} {participle}"

    return card


def suggest_flashcard(german: str, target: str, english: str | None = None) -> CardSuggestion:
    """Propose a short sentence flashcard for a highlighted German word."""
    system = (
        "You build German→English study flashcards for a learner reading a German book. "
        "They highlighted one target word in a sentence; make a clean card that drills it:\n"
        "- german: a SHORT German sentence (about 4–9 words) derived from the SOURCE — keep its structure "
        "and reuse its own nouns and phrases, just TRIM it down to the clause around the target. Don't "
        "paraphrase into unrelated vocabulary; a long literary sentence becomes a short version of itself, "
        "using the same words where you can, with the target word still in it and its meaning unchanged.\n"
        "- For a target verb in a past tense, prefer the PERFEKT — the conversational default in German "
        "(e.g. 'Er ist aus unruhigen Träumen erwacht', not the Präteritum 'Er erwachte'). EXCEPTION: sein, "
        "haben and the modal verbs stay in the Präteritum ('war', 'hatte', 'konnte'), as in real speech. "
        "For a non-past target, keep the source's tense.\n"
        "- english: a natural, faithful English translation of that short sentence (the card front).\n"
        "- target_de: the target word/form to highlight, exactly as it appears in your german sentence; if "
        "the Perfekt splits the verb across the clause, use the past participle (e.g. 'erwacht').\n"
        "- target_en: the word or short phrase in your english sentence that renders the target.\n"
        "- pos: 'noun', 'verb', or 'other'.\n"
        "- lemma: the DICTIONARY form, never the inflected one — a noun WITH its article (e.g. 'das Gemüse'); "
        "a verb's INFINITIVE (e.g. 'erwachte' → 'erwachen', 'lief' → 'laufen'). This is critical: reduce the "
        "highlighted word to its base form.\n"
        "- declension: for a noun fill gender ('der'/'die'/'das') and plural (the plural form, or '—' if none); "
        "for a verb fill the three principal parts — infinitive, preterite (3rd person singular, e.g. 'lief'), "
        "and perfect (3rd-person auxiliary + past participle, e.g. 'ist gelaufen'); leave the rest null.\n"
        "- note: include ONE short Context Note (Markdown) ONLY when a genuinely instructive grammar point "
        "applies to your sentence (a singular/plural mismatch with English, a zero article, a separable verb, "
        "a tricky case). Otherwise return null. Do not pad.\n"
        "Keep everything accurate; the gender, plural and verb parts may be corrected from a dictionary afterwards."
    )
    user = f"Source German sentence: {german}\nHighlighted target word: {target}"
    if english:
        user += f"\nThe book's English for the source: {english}"

    response = _client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _CARD_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if not out:
        raise RuntimeError("No flashcard returned from Claude.")
    card = CardSuggestion.model_validate(json.loads(out))
    return _merge_dictionary_facts(card)


_ANSWER_CHECK_SCHEMA = _strict_schema(AnswerCheck)

# Some models occasionally emit literal "ä" escape text instead of the
# character itself; decode any that slip through so feedback renders cleanly.
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _decode_escapes(text: str) -> str:
    return _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def check_answer(
    english: str, expected_german: str, answer: str, sections: list[GrammarSection]
) -> AnswerCheck:
    """Grade a learner's typed recall of a card, citing grammar sections when relevant."""
    catalogue = _index(sections) if sections else "(no grammar sections available)"
    system = (
        "You are a warm, concise German tutor checking a learner's recall. They were shown an English "
        "sentence and tried to write it in German from memory. You are given the English prompt, the "
        "expected German sentence, the learner's attempt, and a catalogue of grammar sections.\n"
        "Judge whether their attempt conveys the same meaning in grammatical German — accept any natural, "
        "correct paraphrase, not only the expected wording. Set 'correct' accordingly. In 'feedback', start "
        "by acknowledging what they got right, then point out each real mistake (wrong gender, adjective or "
        "verb ending, case, word order, spelling) and give the correction. Be brief and encouraging; do not "
        "nitpick stylistic choices that are already correct. Use Markdown, one short paragraph, and write all "
        "characters directly as UTF-8 (ä, ö, ü, ß, —) — never \\uXXXX escapes.\n"
        "When a mistake is explained by one of the provided grammar sections, list its decimal number in "
        "section_numbers; use ONLY numbers from the catalogue, and leave the list empty if none apply."
    )
    user = (
        f"Available grammar sections:\n\n{catalogue}\n\n---\n\n"
        f"English prompt: {english}\n"
        f"Expected German: {expected_german}\n"
        f"Learner's attempt: {answer}"
    )
    response = _client.messages.create(
        model=MODEL,
        max_tokens=900,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _ANSWER_CHECK_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if not out:
        raise RuntimeError("No answer check returned from Claude.")
    check = AnswerCheck.model_validate(json.loads(out))
    check.feedback = _decode_escapes(check.feedback)
    return check


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


# --- reading: sentence-level alignment ---------------------------------------

_ALIGN_SCHEMA = _strict_schema(AlignedText)

_ALIGN_PROMPT = (
    "You are given the SAME book in two languages: an English text and its German "
    "translation. Align them into sentence-level segments in reading order, so that "
    "each segment pairs one German sentence (or a couple of short ones that must stay "
    "together) with the English that translates it.\n\n"
    "For every segment return:\n"
    "- `german`: the German sentence(s), verbatim from the German text.\n"
    "- `english`: the matching English sentence(s), verbatim from the English text — "
    "never paraphrase.\n\n"
    "If a German sentence has no English counterpart (or vice versa), attach it to the "
    "nearest segment rather than inventing text."
)


_TRANSLATE_PROMPT = (
    "You are given a book in German. Produce a faithful, sentence-aligned English "
    "translation of it.\n\n"
    "Work through the German text in reading order, one segment at a time (a sentence, or "
    "a couple of short sentences that must stay together). For every segment return:\n"
    "- `german`: the German sentence(s), VERBATIM from the source — never reword the German.\n"
    "- `english`: a faithful, natural English translation of that segment — one English "
    "sentence per German sentence, with minimal reordering, so the two line up closely."
)


_CHAPTER_SCHEMA = _strict_schema(DetectedChapters)

_CHAPTER_PROMPT = (
    "You are given the full text of a book in German. Identify its chapter divisions, "
    "if any.\n\n"
    "A chapter division is a structural break the author marked with a heading — for "
    "example a number ('II', '3'), a word ('Erstes Kapitel', 'Kapitel 2', 'Teil I'), a "
    "titled heading ('Drittes Kapitel: Die Reise'), or a centered title line. Headings "
    "sit on their own line, set apart from the surrounding prose. Heading styles vary "
    "between books — recognise the break however it is marked, not by a fixed pattern.\n\n"
    "Return the chapters in reading order. For each:\n"
    "- `title`: the heading EXACTLY as it appears in the text (verbatim).\n"
    "- `start_excerpt`: the first 6-12 words of that chapter's opening sentence of prose, "
    "copied verbatim from the text, so the boundary can be located precisely.\n\n"
    "Only report genuine chapter or section breaks. If the text has no chapter divisions "
    "at all, return an empty list. Never invent headings, and do not treat ordinary "
    "paragraph breaks, scene breaks, or lines of dialogue as chapters."
)


class _OutputLimitError(RuntimeError):
    """Raised when a single ingestion call exhausts the output token budget."""


def _stream_text(content: list[dict[str, Any]], schema: dict[str, Any]) -> str:
    """One streamed structured-output call returning the JSON text block."""

    def _call() -> str:
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

    return _with_retries(_call)


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


def _detect_chapters(german_text: str) -> list[DetectedChapter]:
    """Ask Claude for the book's chapter headings; never fatal to ingestion.

    Runs over the whole text in one pass and returns only the headings plus a short
    opening excerpt per chapter, so the output stays tiny. Any failure (a refusal, a
    too-long text that overflows the context, malformed JSON) degrades to "no chapters"
    rather than failing the upload.
    """
    content = [
        {"type": "text", "text": _CHAPTER_PROMPT},
        {"type": "text", "text": f"=== GERMAN TEXT ===\n\n{german_text}"},
    ]
    try:
        raw = _stream_text(content, _CHAPTER_SCHEMA)
        return DetectedChapters.model_validate_json(raw).chapters
    except Exception:  # noqa: BLE001 — chapter detection is best-effort, never fatal
        return []


_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Collapse whitespace and casefold, for tolerant excerpt matching."""
    return _WS.sub(" ", text).strip().casefold()


def _map_chapters(
    detected: list[DetectedChapter], segments: list[ReadingSegment]
) -> list[ReadingChapter]:
    """Pin each detected chapter to the segment its opening prose falls in.

    Matching the opening excerpt against the aligned German segments is independent of
    how the heading was formatted, so it works the same for any book. Chapters whose
    excerpt can't be located, or that don't advance past the previous one, are dropped;
    the first surviving chapter is anchored to segment 0 so no leading text is orphaned.
    """
    seg_norm = [_norm(s.german) for s in segments]
    chapters: list[ReadingChapter] = []
    search_from = 0
    for ch in detected:
        words = _norm(ch.start_excerpt).split()
        if not words:
            continue
        # Try the full excerpt, then progressively shorter prefixes, in case the model
        # included a word or two beyond where the segment boundary actually falls.
        found: int | None = None
        for length in (len(words), 8, 6, 4):
            needle = " ".join(words[:length])
            if not needle:
                continue
            found = next(
                (i for i in range(search_from, len(segments)) if needle in seg_norm[i]),
                None,
            )
            if found is not None:
                break
        if found is None or (chapters and found <= chapters[-1].start_seq):
            continue
        chapters.append(ReadingChapter(idx=len(chapters), title=ch.title.strip(), start_seq=found))
        search_from = found + 1

    # A book is only "chaptered" if we pinned at least two; otherwise it stays flat.
    if len(chapters) < 2:
        return []
    # Anchor the first chapter to the start so segments before its excerpt aren't lost.
    chapters[0] = ReadingChapter(idx=0, title=chapters[0].title, start_seq=0)
    return chapters


def ingest_book(
    title: str,
    author: str,
    german_text: str,
    english_text: str | None = None,
) -> int:
    """Ingest a German text into a stored, sentence-aligned reading book.

    With only the German text, Claude generates a faithful, sentence-aligned English
    translation (the recommended path — we avoid a published translation's literary
    license breaking the alignment). The German is split into character-bounded chunks
    so a full-length book fits, then all segments are merged in reading order. If a
    faithful English translation is supplied, the two are aligned directly in one pass.
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

    stored_segments = [
        ReadingSegment(seq=i, english=seg.english, german=seg.german)
        for i, seg in enumerate(segments)
    ]
    chapters = _map_chapters(_detect_chapters(german_text), stored_segments)

    from . import db

    return db.create_book(title, author, stored_segments, chapters)
