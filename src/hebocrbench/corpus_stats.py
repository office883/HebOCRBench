"""Deterministic corpus statistics with Hebrew- and BiDi-specific coverage."""

from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence
import unicodedata

import regex

from .unicode_utils import HEBREW_PUNCTUATION, graphemes, is_hebrew_letter


def _line_records(record: Mapping[str, object]):
    regions = record.get("regions", [])
    if not isinstance(regions, list):
        return
    for region in regions:
        if not isinstance(region, Mapping):
            continue
        lines = region.get("lines", [])
        if not isinstance(lines, list):
            continue
        for line in lines:
            if isinstance(line, Mapping):
                yield line


def _mixed_bidi(text: str) -> bool:
    classes = {unicodedata.bidirectional(character) for character in text}
    has_rtl = bool(classes & {"R", "AL"})
    has_ltr_run = bool(classes & {"L", "EN", "AN"})
    return has_rtl and has_ltr_run


def compute_corpus_stats(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    split_counts: Counter[str] = Counter()
    track_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()
    style_counts: Counter[str] = Counter()
    vocalization_counts: Counter[str] = Counter()
    character_counts: Counter[str] = Counter()
    documents: set[str] = set()
    regions = 0
    lines = 0
    codepoints = 0
    grapheme_count = 0
    words = 0
    hebrew_letters = 0
    combining_marks = 0
    hebrew_punctuation = 0
    mixed_bidi_lines = 0
    numeric_runs = 0
    latin_runs = 0
    empty_lines = 0

    for record in records:
        split_counts[str(record.get("split", "<missing>"))] += 1
        track_counts[str(record.get("track", "<missing>"))] += 1
        documents.add(str(record.get("document_id", "<missing>")))
        metadata = record.get("metadata", {})
        if isinstance(metadata, Mapping):
            source_counts[str(metadata.get("source_id", "<missing>"))] += 1
            style_counts[str(metadata.get("script_style", "<missing>"))] += 1
            vocalization_counts[str(metadata.get("vocalization", "<missing>"))] += 1
            raw_languages = metadata.get("languages", [])
            if isinstance(raw_languages, list):
                language_counts.update(str(language) for language in raw_languages)
        raw_regions = record.get("regions", [])
        if isinstance(raw_regions, list):
            regions += len(raw_regions)
        for line in _line_records(record):
            lines += 1
            text = str(line.get("text", ""))
            if not text:
                empty_lines += 1
            codepoints += len(text)
            grapheme_count += len(graphemes(text))
            words += len(regex.findall(r"\S+", text))
            hebrew_letters += sum(is_hebrew_letter(character) for character in text)
            combining_marks += sum(unicodedata.category(character).startswith("M") for character in text)
            hebrew_punctuation += sum(character in HEBREW_PUNCTUATION for character in text)
            mixed_bidi_lines += int(_mixed_bidi(text))
            numeric_runs += len(regex.findall(r"\p{N}+(?:[.,:/-]\p{N}+)*", text))
            latin_runs += len(regex.findall(r"\p{Script=Latin}+(?:[-._]\p{Script=Latin}|[-._\d])*", text))
            character_counts.update(text)

    return {
        "pages": len(records),
        "documents": len(documents),
        "regions": regions,
        "lines": lines,
        "empty_lines": empty_lines,
        "codepoints": codepoints,
        "graphemes": grapheme_count,
        "words_whitespace": words,
        "hebrew_letters": hebrew_letters,
        "combining_marks": combining_marks,
        "hebrew_punctuation": hebrew_punctuation,
        "mixed_bidi_lines": mixed_bidi_lines,
        "numeric_runs": numeric_runs,
        "latin_runs": latin_runs,
        "splits": dict(sorted(split_counts.items())),
        "tracks": dict(sorted(track_counts.items())),
        "sources": dict(sorted(source_counts.items())),
        "languages": dict(sorted(language_counts.items())),
        "script_styles": dict(sorted(style_counts.items())),
        "vocalization": dict(sorted(vocalization_counts.items())),
        "unique_codepoints": len(character_counts),
        "codepoint_histogram": {
            f"U+{ord(character):04X}": count
            for character, count in sorted(character_counts.items(), key=lambda item: ord(item[0]))
        },
    }
