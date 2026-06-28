"""Minimal SRT subtitle parsing — no model, no dependencies.

A listening source's German text and timing come straight from its ``.srt`` file:
each subtitle cue gives a start/end time and the line(s) shown. We parse cues into
plain timestamped text so Claude only has to *curate* (pick interesting spans) and
translate, never invent timings or transcribe audio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "00:01:02,500" or "00:01:02.500" -> milliseconds; SRT uses a comma, some tools a dot.
_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_ARROW = re.compile(r"-->")
# Strip the simple inline markup subtitles sometimes carry.
_TAG = re.compile(r"<[^>]+>|\{[^}]*\}")


@dataclass
class Cue:
    """One subtitle cue: when it shows and the (tag-stripped) text it shows."""

    start_ms: int
    end_ms: int
    text: str


def _to_ms(match: re.Match[str]) -> int:
    h, m, s, frac = match.groups()
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def _clean(lines: list[str]) -> str:
    """Join a cue's text lines into one space-separated, tag-free string."""
    joined = " ".join(line.strip() for line in lines if line.strip())
    return _TAG.sub("", joined).strip()


def parse(content: str) -> list[Cue]:
    """Parse SRT text into cues in order, skipping any malformed blocks.

    Blocks are separated by blank lines; a block's first line with a ``-->`` carries
    the timing, and the lines after it are the text. The leading sequence number is
    optional and ignored. Cues with no parseable timing or no text are dropped.
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    cues: list[Cue] = []
    for block in re.split(r"\n[ \t]*\n", content):
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if _ARROW.search(ln)), None)
        if timing_idx is None:
            continue
        times = _TIME.findall(lines[timing_idx])
        if len(times) < 2:
            continue
        start, _, end = lines[timing_idx].partition("-->")
        start_match, end_match = _TIME.search(start), _TIME.search(end)
        if not start_match or not end_match:
            continue
        text = _clean(lines[timing_idx + 1 :])
        if not text:
            continue
        cues.append(Cue(start_ms=_to_ms(start_match), end_ms=_to_ms(end_match), text=text))
    return cues
