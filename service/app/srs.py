"""Spaced-repetition scheduling for flashcards (a compact SM-2 variant).

Cards are reviewed at day granularity. After each review the learner grades
recall on a four-button scale — ``again`` / ``hard`` / ``good`` / ``easy`` — and
this module returns the card's next scheduling state: how many days until it's
due again, the adjusted ease factor, and the running rep/lapse counts.

``again`` resets the learning streak and the card comes back the same day; the
other grades grow the interval geometrically by the ease factor, the classic
SM-2 behaviour with Anki-style first/second intervals of 1 and 6 days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

# SM-2 quality score (0–5) for each button; drives the ease adjustment below.
_QUALITY = {"again": 2, "hard": 3, "good": 4, "easy": 5}
_MIN_EASE = 1.3
_DEFAULT_EASE = 2.5
# First two successful intervals are fixed (days); afterwards interval *= ease.
_FIRST_INTERVAL = 1.0
_SECOND_INTERVAL = 6.0
_HARD_INTERVAL = 1.0  # a 'hard' on a brand-new card still comes back tomorrow
_EASY_BONUS = 1.3


@dataclass
class Schedule:
    """The scheduling state produced for a card after one review."""

    interval: float
    ease: float
    reps: int
    lapses: int
    due: str  # ISO date (YYYY-MM-DD)


def initial(today: date | None = None) -> Schedule:
    """Scheduling state for a freshly created card: due immediately."""
    today = today or _today()
    return Schedule(interval=0.0, ease=_DEFAULT_EASE, reps=0, lapses=0, due=today.isoformat())


def review(
    rating: str,
    interval: float,
    ease: float,
    reps: int,
    lapses: int,
    today: date | None = None,
) -> Schedule:
    """Apply a grade to a card's current state and return its next state."""
    if rating not in _QUALITY:
        raise ValueError(f"Unknown rating: {rating!r}")
    today = today or _today()
    quality = _QUALITY[rating]

    if rating == "again":
        # Forgotten: reset the streak, drop ease, requeue for the same day.
        ease = max(_MIN_EASE, ease - 0.2)
        return Schedule(interval=0.0, ease=ease, reps=0, lapses=lapses + 1, due=today.isoformat())

    # SM-2 ease update, then pick the next interval from the rep count.
    ease = max(_MIN_EASE, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
    if reps == 0:
        next_interval = _HARD_INTERVAL if rating == "hard" else _FIRST_INTERVAL
    elif reps == 1:
        next_interval = _SECOND_INTERVAL
    else:
        factor = ease * (0.85 if rating == "hard" else _EASY_BONUS if rating == "easy" else 1.0)
        next_interval = max(1.0, round(interval * factor))

    due = date.fromordinal(today.toordinal() + int(round(next_interval)))
    return Schedule(interval=next_interval, ease=ease, reps=reps + 1, lapses=lapses, due=due.isoformat())


def _today() -> date:
    return datetime.now(timezone.utc).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
