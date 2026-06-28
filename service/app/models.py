"""Pydantic models shared across the grammar service.

The shapes mirror how Hammer's German Grammar and Usage is organized: numbered
chapters containing hierarchically-numbered sections (e.g. ``12``, ``12.3``,
``12.3.2``). Practice exercises (from Practising German Grammar) are keyed back
to the Hammer's section they drill, so theory and practice join on the section
number.

These models double as the JSON-schema contract for Claude's structured-output
extraction (``claude_client.py``) and as the API response shapes (``main.py``).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# --- Theory: chapters + sections --------------------------------------------


class Chapter(BaseModel):
    """A top-level chapter heading, e.g. '14  The modal auxiliaries'."""

    number: int = Field(description="The chapter number, e.g. 14.")
    title: str = Field(description="The chapter title, without the leading number.")


class GrammarSectionData(BaseModel):
    """One numbered grammar section as written in the book.

    ``number`` is the book's own decimal section number and is the natural key.
    Chapter / parent / level are derived from it at storage time, so the model
    only needs to report the number accurately.
    """

    number: str = Field(
        description="The book's decimal section number exactly as printed, e.g. '12.3.2'."
    )
    title: str = Field(description="The section heading, without the leading number.")
    summary: str = Field(
        description="One or two sentence plain-language summary of what this section teaches."
    )
    rule: str = Field(
        description="The full rule and explanation in Markdown, preserving tables, "
        "conjugations, and lists as they appear in the book."
    )
    examples: list[str] = Field(
        default_factory=list,
        description="Example sentences (with English glosses if the book gives them).",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="German words and grammatical terms this section governs, for word-to-rule lookup.",
    )
    cross_references: list[str] = Field(
        default_factory=list,
        description="Other section numbers this section explicitly refers to, e.g. ['2.1', '14.4'].",
    )


class ExtractedGrammar(BaseModel):
    """What Claude returns for one window of pages from the theory book."""

    chapters: list[Chapter] = Field(
        default_factory=list,
        description="Any chapter headings that begin on these pages.",
    )
    sections: list[GrammarSectionData] = Field(
        default_factory=list,
        description="Every grammar section whose text appears on these pages.",
    )


# --- Practice: exercises -----------------------------------------------------


class ExerciseItem(BaseModel):
    """One numbered item within an exercise."""

    prompt: str = Field(description="The item itself, e.g. the noun 'Regen' or a sentence to complete.")
    answer: Optional[str] = Field(
        default=None,
        description="The answer if an answer key is present, else null.",
    )


class ExerciseData(BaseModel):
    """A whole workbook exercise — a unit that may span a range of GGU sections."""

    chapter_number: int = Field(
        description="The workbook chapter number (matches the Hammer's/GGU chapter), e.g. 1."
    )
    label: str = Field(description="The exercise number/label as printed, e.g. '5'.")
    title: str = Field(description="The exercise title, e.g. 'Gender'.")
    instructions: str = Field(description="What the learner is asked to do.")
    section_refs: list[str] = Field(
        default_factory=list,
        description="The GGU section references this exercise practises, exactly as printed — "
        "single numbers or ranges, e.g. ['1.1.1–1.1.9'] or ['12.3', '12.4'].",
    )
    items: list[ExerciseItem] = Field(
        default_factory=list,
        description="The individual items of the exercise, in order.",
    )


class ExtractedExercises(BaseModel):
    """What Claude returns for one window of pages from the practice book."""

    exercises: list[ExerciseData] = Field(default_factory=list)


# --- Persisted / response shapes --------------------------------------------


class GrammarSection(GrammarSectionData):
    """A stored section: the extracted shape plus derived hierarchy fields."""

    chapter_number: int
    parent_number: Optional[str]
    level: int


class Exercise(ExerciseData):
    id: int


class ChapterWithSections(Chapter):
    sections: list[GrammarSection] = Field(default_factory=list)


# --- Ask ---------------------------------------------------------------------


class AskRequest(BaseModel):
    query: str = Field(description="A German word, phrase, or free-text grammar question.")


class AskResponse(BaseModel):
    answer: str = Field(description="Claude's explanation grounded in the stored grammar sections.")
    section_numbers: list[str] = Field(
        default_factory=list,
        description="Section numbers Claude relied on, e.g. ['12.3', '14.4'].",
    )


class SectionSelection(BaseModel):
    """Which sections a lightweight index call judged relevant to a query.

    Used to retrieve only the handful of full grammar rules worth sending to the
    explanation call, instead of the whole book's text every time.
    """

    numbers: list[str] = Field(
        default_factory=list,
        description="Decimal section numbers relevant to the query, most relevant first; "
        "use only numbers present in the index, and return an empty list if none apply.",
    )


# --- Selection: translate / dictionary ---------------------------------------
#
# When the learner highlights a span while reading, the client offers three
# actions on it: a plain translation (Sonnet), a grounded grammar explanation,
# and a free-form question. The translation is enriched, for single-word
# selections, with deterministic dictionary facts from a Wiktionary-backed API
# so gender / plural / verb forms never depend on the model.


class WordForm(BaseModel):
    """A single labelled inflected form, e.g. label='plural', form='Häuser'."""

    label: str
    form: str


class DictionaryEntry(BaseModel):
    """Deterministic grammatical facts for one German word (no model involved)."""

    word: str = Field(description="The headword that was looked up.")
    part_of_speech: str
    gender: Optional[str] = Field(
        default=None, description="Display article for a noun: 'der' | 'die' | 'das'."
    )
    gender_label: Optional[str] = Field(
        default=None, description="'masculine' | 'feminine' | 'neuter', if known."
    )
    pronunciation: Optional[str] = Field(default=None, description="IPA, if available.")
    forms: list[WordForm] = Field(
        default_factory=list,
        description="Key forms: plural/genitive for nouns, principal parts for verbs, etc.",
    )
    definitions: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None


class SelectionTranslation(BaseModel):
    """Claude's structured output for a selection translation."""

    translation: str = Field(description="Faithful, natural English translation of the German text.")
    note: Optional[str] = Field(
        default=None,
        description="One brief usage note (idiom, case, separable verb, register) if helpful, else null.",
    )


class TranslateRequest(BaseModel):
    text: str = Field(description="The German text the learner selected.")
    context: Optional[str] = Field(
        default=None, description="The enclosing German sentence, for disambiguation."
    )


class TranslateResponse(BaseModel):
    translation: str
    note: Optional[str] = None
    dictionary: Optional[DictionaryEntry] = Field(
        default=None, description="Dictionary facts when the selection is a single word."
    )


class GrammarContextRequest(BaseModel):
    text: str = Field(description="The German text the learner selected.")
    context: Optional[str] = Field(default=None, description="The enclosing German sentence.")


class FreeAskRequest(BaseModel):
    text: str = Field(description="The German text the learner selected.")
    question: str = Field(description="The learner's free-form question about it.")
    context: Optional[str] = Field(default=None, description="The enclosing German sentence.")


# --- Assessing exercise answers ----------------------------------------------


class SubmittedAnswer(BaseModel):
    index: int = Field(description="0-based index of the exercise item this answer is for.")
    answer: str = Field(description="The learner's answer for that item.")


class AssessRequest(BaseModel):
    answers: list[SubmittedAnswer]


class ItemAssessment(BaseModel):
    index: int = Field(description="0-based index of the item being graded.")
    correct: bool = Field(description="Whether the learner's answer is correct.")
    correct_answer: str = Field(description="The expected correct answer.")
    comment: str = Field(
        description="A short explanation — especially why a wrong answer is wrong. Empty if trivially correct."
    )
    section_numbers: list[str] = Field(
        default_factory=list,
        description="GGU section number(s) explaining this item's rule (from the provided sections only).",
    )


class AssessmentResult(BaseModel):
    items: list[ItemAssessment] = Field(description="One assessment per submitted item, in order.")
    summary: str = Field(
        description="Brief overall feedback: how the learner did and which grammar areas to focus on."
    )
    review_sections: list[str] = Field(
        default_factory=list,
        description="Section numbers the learner should review, based on their mistakes.",
    )


# --- Reading: diglot-weave books --------------------------------------------
#
# A reading book is a parallel English/German text aligned at the sentence level.
# Each aligned segment is broken into chunks: plain English run-on text plus
# "weaveable" content words that carry a contextually-correct German replacement.
# The diglot weave is rendered on the client by swapping in the German form of a
# chunk once its global frequency rank falls under the chosen density threshold.


class WeaveChunkData(BaseModel):
    """One chunk of an aligned English segment, as Claude returns it.

    Concatenating every chunk's ``text`` in order must reproduce the original
    English sentence verbatim (spacing and punctuation included). A chunk with a
    non-null ``de`` is weaveable — the German form can replace its English text.
    """

    text: str = Field(
        description="The English surface text of this chunk, verbatim. Plain runs carry "
        "the spacing and punctuation; a weaveable chunk is just the word/phrase itself."
    )
    de: Optional[str] = Field(
        default=None,
        description="The contextually-correct German form to weave in for this chunk "
        "(with article for nouns, correctly inflected), or null if this chunk is not weaveable.",
    )
    gloss: Optional[str] = Field(
        default=None,
        description="A short English meaning shown when the learner taps the woven word, "
        "or null for non-weaveable chunks.",
    )


class AlignedSegmentData(BaseModel):
    """One sentence-level aligned pair as Claude returns it."""

    german: str = Field(description="The aligned German sentence(s), verbatim from the translation.")
    chunks: list[WeaveChunkData] = Field(
        default_factory=list,
        description="The English sentence split into ordered chunks (plain text + weaveable words). "
        "Concatenating every chunk's `text` must reproduce the English sentence exactly.",
    )


class AlignedText(BaseModel):
    """What Claude returns for an aligned English/German book."""

    segments: list[AlignedSegmentData] = Field(default_factory=list)


# --- persisted / response shapes --------------------------------------------


class StoredChunk(WeaveChunkData):
    """A weave chunk plus its global frequency rank (null when not weaveable)."""

    rank: Optional[int] = Field(
        default=None,
        description="0-based frequency rank of this word's lemma across the book "
        "(0 = most frequent, woven in first). Null for non-weaveable chunks.",
    )


class ReadingSegment(BaseModel):
    seq: int = Field(description="0-based position of this segment within the book.")
    english: str = Field(description="The plain English sentence (derived from the chunks).")
    german: str
    chunks: list[StoredChunk] = Field(default_factory=list)


class Book(BaseModel):
    id: int
    title: str
    author: str
    vocab_size: int = Field(description="Number of distinct weaveable lemmas in the book.")
    segment_count: int


class BookDetail(Book):
    segments: list[ReadingSegment] = Field(default_factory=list)


# --- Flashcards: sentence cards with one target word -------------------------
#
# A card built from a reading selection: an English/German sentence pair with one
# word highlighted, whose inflection the learner recalls. For a noun that's the
# gender + plural; for a verb the three principal parts (Stammformen). An optional
# Context Note carries a short grammar point when one genuinely applies. Cards are
# scheduled in-app with an SM-2 spaced-repetition state (see ``srs.py``).


class CardDeclension(BaseModel):
    """The inflection the learner is tested on for the card's target word.

    Only the fields relevant to ``pos`` are filled: noun → gender + plural,
    verb → the three principal parts. ``other`` parts of speech leave all null.
    """

    gender: Optional[str] = Field(
        default=None, description="Noun article: 'der' | 'die' | 'das'."
    )
    plural: Optional[str] = Field(
        default=None,
        description="The noun's plural form, or '—' when it has no distinct plural.",
    )
    infinitive: Optional[str] = Field(default=None, description="Verb infinitive, e.g. 'laufen'.")
    preterite: Optional[str] = Field(
        default=None, description="Verb 3rd-person-singular preterite, e.g. 'lief'."
    )
    perfect: Optional[str] = Field(
        default=None,
        description="Verb perfect with auxiliary + past participle, e.g. 'ist gelaufen'.",
    )


class CardSuggestion(BaseModel):
    """A proposed flashcard for a selection, before the learner saves it.

    The prose fields come from Claude; the deterministic declension facts are
    overridden by the Wiktionary dictionary when it resolves the word.
    """

    english: str = Field(description="Natural English translation of the whole sentence (the card front).")
    german: str = Field(description="The German sentence, verbatim (the card back).")
    target_de: str = Field(description="The German target word as it appears in the sentence, to highlight.")
    target_en: str = Field(description="The English word(s) in the translation that render the target, to highlight.")
    pos: str = Field(description="Part of speech of the target: 'noun' | 'verb' | 'other'.")
    lemma: str = Field(
        description="Dictionary form of the target: a noun with its article ('das Gemüse') or a verb infinitive ('mögen')."
    )
    declension: CardDeclension = Field(
        default_factory=CardDeclension,
        description="Inflection to test: gender+plural for nouns, the three principal parts for verbs.",
    )
    note: Optional[str] = Field(
        default=None,
        description="One short Context Note (Markdown) on a genuinely instructive grammar point, or null if none applies.",
    )
    section_numbers: list[str] = Field(
        default_factory=list, description="Grammar section numbers the note draws on, if any."
    )


class CardSuggestRequest(BaseModel):
    german: str = Field(description="The enclosing German sentence.")
    target: str = Field(description="The German word/phrase the learner highlighted.")
    english: Optional[str] = Field(
        default=None, description="The known English sentence, if the reader already has it."
    )


class FlashcardData(BaseModel):
    """A card the learner has chosen to save (the editable suggestion)."""

    book_id: Optional[int] = Field(default=None, description="The reading book this card came from, if any.")
    english: str
    german: str
    target_de: str
    target_en: str
    pos: str
    lemma: str
    declension: CardDeclension = Field(default_factory=CardDeclension)
    note: Optional[str] = None
    section_numbers: list[str] = Field(default_factory=list)


class Flashcard(FlashcardData):
    """A stored card plus its spaced-repetition scheduling state."""

    id: int
    due: str = Field(description="ISO date (YYYY-MM-DD) the card is next due for review.")
    interval: float = Field(description="Current inter-review interval in days.")
    ease: float = Field(description="SM-2 ease factor (>= 1.3).")
    reps: int = Field(description="Successful reviews in a row.")
    lapses: int = Field(description="Times the card was forgotten (rated 'again').")
    last_reviewed: Optional[str] = Field(default=None, description="ISO datetime of the last review, or null.")
    created_at: str = Field(description="ISO datetime the card was created.")


class ReviewRequest(BaseModel):
    rating: str = Field(description="The learner's grade: 'again' | 'hard' | 'good' | 'easy'.")


class AnswerCheckRequest(BaseModel):
    answer: str = Field(description="The German sentence the learner typed from memory.")


class AnswerCheck(BaseModel):
    """Claude's assessment of a learner's typed recall of a card."""

    correct: bool = Field(
        description="True if the answer conveys the same meaning in grammatical German "
        "(an acceptable paraphrase counts), even if it isn't word-for-word the expected sentence."
    )
    feedback: str = Field(
        description="Brief, encouraging Markdown feedback: what was right, then any mistakes "
        "(gender, ending, case, word order, spelling) with the correction. One short paragraph. "
        "Write all characters directly as UTF-8 (ä, ö, ü, ß, —); never use \\uXXXX escapes."
    )
    section_numbers: list[str] = Field(
        default_factory=list,
        description="Decimal grammar section numbers (from the provided sections only) whose rules "
        "explain the learner's mistakes, e.g. ['2.1', '21.2']. Empty if none apply.",
    )
