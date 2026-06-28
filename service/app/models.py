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


# --- Reading: parallel German/English books ---------------------------------
#
# A reading book is a parallel text aligned at the sentence level: each segment
# pairs the verbatim German sentence(s) with a faithful English translation. The
# reader shows the German prose and reveals a segment's English on tap; any
# highlighted span can be looked up via the selection actions above.
#
# When the source text is divided into chapters, a separate detection pass finds
# the headings and records which segment each chapter begins at, so the book can
# be read a chapter at a time. Books with no detectable chapters stay flat.


class AlignedSegmentData(BaseModel):
    """One sentence-level aligned pair as Claude returns it."""

    german: str = Field(description="The German sentence(s), verbatim from the source text.")
    english: str = Field(description="A faithful English translation of the German sentence(s).")


class AlignedText(BaseModel):
    """What Claude returns for an aligned German/English book."""

    segments: list[AlignedSegmentData] = Field(default_factory=list)


class DetectedChapter(BaseModel):
    """One chapter heading Claude found in the source text."""

    title: str = Field(
        description="The chapter heading EXACTLY as printed, e.g. 'II', 'Erstes Kapitel', "
        "'Chapter 3: The Letter'."
    )
    start_excerpt: str = Field(
        description="The first 6-12 words of this chapter's opening sentence of prose, copied "
        "verbatim from the text, so the boundary can be located precisely."
    )


class DetectedChapters(BaseModel):
    """What Claude returns when asked to find a book's chapter divisions."""

    chapters: list[DetectedChapter] = Field(
        default_factory=list,
        description="Chapters in reading order; empty if the text has no chapter divisions.",
    )


# --- persisted / response shapes --------------------------------------------


class ReadingSegment(BaseModel):
    seq: int = Field(description="0-based position of this segment within the book.")
    english: str
    german: str


class ReadingChapter(BaseModel):
    """A stored chapter: its heading and the segment it begins at."""

    idx: int = Field(description="0-based position of this chapter within the book.")
    title: str
    start_seq: int = Field(description="Seq of the first segment belonging to this chapter.")


class ChapterMeta(BaseModel):
    """A chapter as listed in a book's table of contents."""

    idx: int
    title: str
    segment_count: int


class Book(BaseModel):
    id: int
    title: str
    author: str
    segment_count: int
    chapter_count: int = Field(description="Number of chapters (1 when the book has no divisions).")


class BookDetail(Book):
    chapters: list[ChapterMeta] = Field(default_factory=list)


class ChapterDetail(BaseModel):
    """One chapter's readable content plus navigation to its neighbours."""

    book_id: int
    book_title: str
    idx: int
    title: str
    prev_idx: Optional[int] = None
    next_idx: Optional[int] = None
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
        description="A short Context Note (Markdown) on the instructive grammar across the whole sentence — "
        "the target word and any other notable feature — or null if the sentence is wholly unremarkable.",
    )
    section_numbers: list[str] = Field(
        default_factory=list,
        description="Decimal grammar section numbers (from the provided catalogue) whose rules explain the note.",
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


# --- Listening: video + SRT into dictation clips -----------------------------
#
# A listening source is a local video paired with its subtitle (.srt) file. On
# ingestion the SRT is parsed into timed cues; Claude curates the most useful
# conversational spans, translates each into English, and tags difficulty + topic.
# Crucially the model never invents timestamps or German text — it only points at
# which cues form a clip; the start/end milliseconds and the verbatim German come
# straight from the SRT. Each clip is reviewed like a flashcard (same SM-2 state):
# the learner hears it, transcribes the German by ear, and the attempt is checked
# against the true transcript. Any span of the revealed transcript can then be
# looked up with the same selection actions reading offers.


class SelectedClip(BaseModel):
    """One clip Claude curated from a window of subtitle cues.

    ``start_index``/``end_index`` are 0-based positions into the window's cue list
    (inclusive); the service maps them back to real timestamps and verbatim German,
    so the model is trusted only for curation, translation, and tagging.
    """

    start_index: int = Field(description="0-based index of the clip's first cue within the window.")
    end_index: int = Field(description="0-based index of the clip's last cue within the window (inclusive).")
    english: str = Field(description="Faithful, natural English translation of the clip's combined German.")
    difficulty: str = Field(description="CEFR level of the clip: 'A1' | 'A2' | 'B1' | 'B2' | 'C1'.")
    topic: str = Field(description="A short 2-4 word topic label, e.g. 'ordering food' or 'small talk'.")


class CuratedClips(BaseModel):
    """What Claude returns for one window of subtitle cues."""

    clips: list[SelectedClip] = Field(
        default_factory=list,
        description="The most useful conversational clips in this window, in order; empty if none stand out.",
    )


class ListeningSourceData(BaseModel):
    """The local media a listening source is built from."""

    title: str = Field(description="Display title for the source, e.g. the episode name.")
    video_path: str = Field(description="Absolute path to the local video/audio file on disk.")


class ListeningSource(ListeningSourceData):
    """A stored listening source plus how many clips were curated from it."""

    id: int
    clip_count: int
    created_at: str


class ListeningClipData(BaseModel):
    """A listening clip's editable content (everything bar its SM-2 schedule)."""

    source_id: int = Field(description="The listening source this clip belongs to.")
    seq: int = Field(description="0-based order of the clip within its source.")
    start_ms: int = Field(description="Clip start within the media, in milliseconds.")
    end_ms: int = Field(description="Clip end within the media, in milliseconds.")
    transcript_de: str = Field(description="The German transcript, verbatim from the SRT cues.")
    transcript_en: str = Field(description="The English translation of the clip.")
    difficulty: str = Field(description="CEFR level, e.g. 'B1'.")
    topic: str = Field(description="Short topic label.")


class ListeningClip(ListeningClipData):
    """A stored listening clip plus its spaced-repetition scheduling state."""

    id: int
    due: str = Field(description="ISO date (YYYY-MM-DD) the clip is next due for review.")
    interval: float = Field(description="Current inter-review interval in days.")
    ease: float = Field(description="SM-2 ease factor (>= 1.3).")
    reps: int = Field(description="Successful reviews in a row.")
    lapses: int = Field(description="Times the clip was forgotten (rated 'again').")
    last_reviewed: Optional[str] = Field(default=None, description="ISO datetime of the last review, or null.")
    created_at: str = Field(description="ISO datetime the clip was created.")


class DictationCheckRequest(BaseModel):
    answer: str = Field(description="The German the learner typed from listening to the clip.")


# --- Unified review feed -----------------------------------------------------
#
# Vocab flashcards and listening clips are reviewed together in one queue. The
# feed tags each due item with its ``kind`` and carries the matching card so the
# client can render and grade it through the right (vocab / listening) endpoints.


class ReviewItem(BaseModel):
    """One due item in the mixed review queue, tagged by kind."""

    kind: str = Field(description="'vocab' | 'listening'.")
    due: str = Field(description="ISO date the item is due (used to order the queue).")
    vocab: Optional["Flashcard"] = Field(default=None, description="The card when kind == 'vocab'.")
    listening: Optional[ListeningClip] = Field(default=None, description="The clip when kind == 'listening'.")
