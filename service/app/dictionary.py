"""Single-word lookups against the free, Wiktionary-backed dictionary API.

This supplies the *deterministic* grammatical facts — gender, plural, genitive,
principal verb parts, comparison forms — that we never want a language model to
guess at. Claude still writes the prose translation and explanation; this module
just hands back sourced facts to display alongside it.

Everything here is best-effort: any failure (network, missing word, unexpected
shape) returns ``None`` so the caller falls back to Claude alone.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Optional

from .models import DictionaryEntry, WordForm

_API = "https://freedictionaryapi.com/api/v1/entries/de/"
_TIMEOUT = 6.0

# Articles/determiners stripped from a 'der Wolf'-style selection before lookup.
_ARTICLES = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines"}
_GENDERS = ("masculine", "feminine", "neuter")
_ARTICLE_OF = {"masculine": "der", "feminine": "die", "neuter": "das"}
# Parts of speech we'd rather surface than a proper-name / interjection entry.
_PREFERRED_POS = ("noun", "verb", "adjective", "adverb")
_STRIP = ".,;:!?\"'()[]»«„“”’‚‘…—–-"


def _headword(text: str) -> Optional[str]:
    """Reduce a selection to a single lookup word, or None if it isn't one."""
    words = text.strip().split()
    if len(words) == 2 and words[0].lower() in _ARTICLES:
        words = words[1:]  # 'der Wolf' -> 'Wolf'
    if len(words) != 1:
        return None
    word = words[0].strip(_STRIP).strip()
    return word or None


def _find_form(forms: list[dict[str, Any]], *required: str, exclude: tuple[str, ...] = ()) -> Optional[str]:
    """First form whose tags include all `required` and none of `exclude`."""
    req, exc = set(required), set(exclude)
    for f in forms:
        tags = set(f.get("tags", []))
        if req.issubset(tags) and not (exc & tags):
            word = (f.get("word") or "").strip()
            if word:
                return word
    return None


def _gender(entry: dict[str, Any]) -> Optional[str]:
    """Gender for a noun. Senses carry it most reliably; forms are a fallback.

    Gender isn't always on the first sense (e.g. 'Tisch' has it only on a later
    sense), so every sense is scanned. Diminutive forms are all neuter and would
    mislead, so they're skipped in the form fallback.
    """
    for sense in entry.get("senses", []):
        for g in _GENDERS:
            if g in sense.get("tags", []):
                return g
    for f in entry.get("forms", []):
        tags = f.get("tags", [])
        if "diminutive" in tags:
            continue
        for g in _GENDERS:
            if g in tags:
                return g
    return None


def _forms_for(pos: str, forms: list[dict[str, Any]]) -> list[WordForm]:
    """The handful of key forms worth showing for this part of speech."""
    out: list[WordForm] = []

    def add(label: str, value: Optional[str]) -> None:
        if value:
            out.append(WordForm(label=label, form=value))

    if pos == "noun":
        add("plural", _find_form(forms, "plural", "nominative") or _find_form(forms, "plural"))
        add("genitive", _find_form(forms, "genitive", "singular"))
    elif pos == "verb":
        add(
            "present (3rd sg)",
            _find_form(forms, "present", "third-person", "singular", "indicative")
            or _find_form(forms, "present", "third-person", "singular"),
        )
        add(
            "preterite",
            _find_form(forms, "preterite", "third-person", "singular", "indicative")
            or _find_form(forms, "preterite", "singular")
            or _find_form(forms, "past", exclude=("participle",)),
        )
        add("past participle", _find_form(forms, "participle", "past"))
        add("auxiliary", _find_form(forms, "auxiliary"))
    elif pos == "adjective":
        add("comparative", _find_form(forms, "comparative"))
        add("superlative", _find_form(forms, "superlative"))

    return out


def _pick_entry(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer a content-word entry over proper names / particles."""
    for entry in entries:
        if entry.get("partOfSpeech") in _PREFERRED_POS:
            return entry
    return entries[0]


def lookup(text: str) -> Optional[DictionaryEntry]:
    """Dictionary facts for a single-word selection, or None if unresolvable."""
    word = _headword(text)
    if not word:
        return None

    try:
        url = _API + urllib.parse.quote(word)
        request = urllib.request.Request(url, headers={"User-Agent": "language-learning/1.0"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — any failure means "no dictionary data"
        return None

    entries = data.get("entries") or []
    if not entries:
        return None

    entry = _pick_entry(entries)
    pos = entry.get("partOfSpeech") or "word"
    forms = entry.get("forms") or []

    gender_label = _gender(entry) if pos == "noun" else None

    pronunciation = None
    for p in entry.get("pronunciations") or []:
        if p.get("type") == "ipa" and p.get("text"):
            pronunciation = p["text"]
            break

    definitions: list[str] = []
    for sense in entry.get("senses") or []:
        definition = (sense.get("definition") or "").strip()
        if definition and definition not in definitions:
            definitions.append(definition)
        if len(definitions) >= 3:
            break

    return DictionaryEntry(
        word=data.get("word", word),
        part_of_speech=pos,
        gender=_ARTICLE_OF.get(gender_label or ""),
        gender_label=gender_label,
        pronunciation=pronunciation,
        forms=_forms_for(pos, forms),
        definitions=definitions,
        source_url=(data.get("source") or {}).get("url"),
    )
