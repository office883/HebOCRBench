"""Unicode and Hebrew-script helpers.

Strict scoring operates on logical-order NFC text. Directional formatting
characters are invisible layout metadata and are excluded from edit distance,
while :func:`bidi_hygiene` reports them separately.
"""

from __future__ import annotations

from collections import Counter
import unicodedata

import regex

# Explicit directional formatting and isolate controls from UAX #9.
BIDI_CONTROLS = frozenset(
    {
        "\u061c",  # ARABIC LETTER MARK
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u202a",  # LEFT-TO-RIGHT EMBEDDING
        "\u202b",  # RIGHT-TO-LEFT EMBEDDING
        "\u202c",  # POP DIRECTIONAL FORMATTING
        "\u202d",  # LEFT-TO-RIGHT OVERRIDE
        "\u202e",  # RIGHT-TO-LEFT OVERRIDE
        "\u2066",  # LEFT-TO-RIGHT ISOLATE
        "\u2067",  # RIGHT-TO-LEFT ISOLATE
        "\u2068",  # FIRST STRONG ISOLATE
        "\u2069",  # POP DIRECTIONAL ISOLATE
    }
)

BIDI_MARKS = frozenset({"\u061c", "\u200e", "\u200f"})
BIDI_EMBEDDINGS = frozenset({"\u202a", "\u202b", "\u202c"})
BIDI_OVERRIDES = frozenset({"\u202d", "\u202e"})
BIDI_ISOLATES = frozenset({"\u2066", "\u2067", "\u2068", "\u2069"})

ZERO_WIDTH_CONTROLS = frozenset(
    {
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u2060",  # WORD JOINER
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE / BOM
    }
)

HEBREW_PRESENTATION_FORMS_RANGE = range(0xFB1D, 0xFB50)
HEBREW_FINAL_TO_MEDIAL = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
HEBREW_MEDIAL_TO_FINAL = {v: k for k, v in HEBREW_FINAL_TO_MEDIAL.items()}
HEBREW_PUNCTUATION = frozenset("־׀׃׳״")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def remove_bidi_controls(text: str) -> str:
    return "".join(ch for ch in text if ch not in BIDI_CONTROLS)


def normalize_strict(text: str) -> str:
    """Return the canonical strict-scoring representation.

    This function deliberately does not trim, collapse whitespace, remove
    niqqud, map punctuation, change final letters, or reverse the string.
    """

    text = normalize_newlines(text)
    text = remove_bidi_controls(text)
    return unicodedata.normalize("NFC", text)


def graphemes(text: str) -> list[str]:
    """Segment text into Unicode extended grapheme clusters (UAX #29)."""

    return regex.findall(r"\X", text)


def is_hebrew_letter(char: str) -> bool:
    return len(char) == 1 and 0x05D0 <= ord(char) <= 0x05EA


def is_hebrew_mark(char: str) -> bool:
    if len(char) != 1:
        return False
    cp = ord(char)
    return (0x0591 <= cp <= 0x05BD) or cp in {
        0x05BF,
        0x05C1,
        0x05C2,
        0x05C4,
        0x05C5,
        0x05C7,
    }


def classify_hebrew_mark(char: str) -> str | None:
    """Map a Hebrew combining mark to a stable benchmark category."""

    if len(char) != 1:
        return None
    cp = ord(char)
    if 0x0591 <= cp <= 0x05AF:
        return "cantillation"
    if (0x05B0 <= cp <= 0x05BB) or cp == 0x05C7:
        return "vowel"
    if cp == 0x05BC:
        return "dagesh_mapiq"
    if cp in {0x05C1, 0x05C2}:
        return "shin_sin_dot"
    if cp in {0x05BD, 0x05BF}:
        return "meteg_rafe"
    if cp in {0x05C4, 0x05C5}:
        return "other_hebrew_mark"
    return None


def split_base_and_marks(cluster: str) -> tuple[str, tuple[str, ...]]:
    """Split a grapheme cluster into visible base content and combining marks."""

    decomposed = unicodedata.normalize("NFD", cluster)
    bases: list[str] = []
    marks: list[str] = []
    for ch in decomposed:
        if unicodedata.category(ch).startswith("M"):
            marks.append(ch)
        else:
            bases.append(ch)
    return "".join(bases), tuple(marks)


def strip_hebrew_marks(text: str) -> str:
    """Remove Hebrew marks for the diagnostic base-letter profile.

    NFKD intentionally decomposes compatibility presentation forms in this
    secondary profile. The strict profile still exposes their use.
    """

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not is_hebrew_mark(ch))
    return unicodedata.normalize("NFC", stripped)


def has_hebrew_presentation_forms(text: str) -> bool:
    return any(ord(ch) in HEBREW_PRESENTATION_FORMS_RANGE for ch in text)


def codepoint_view(text: str) -> list[dict[str, str | int]]:
    """Return a safe logical-order code-point representation for error viewers."""

    return [
        {
            "index": index,
            "char": ch,
            "codepoint": f"U+{ord(ch):04X}",
            "name": unicodedata.name(ch, "<UNNAMED>"),
            "bidi": unicodedata.bidirectional(ch),
            "category": unicodedata.category(ch),
        }
        for index, ch in enumerate(text)
    ]


def dangling_combining_mark_indices(text: str) -> list[int]:
    """Find combining marks that do not follow a usable base in their cluster."""

    indices: list[int] = []
    offset = 0
    for cluster in graphemes(text):
        base, marks = split_base_and_marks(cluster)
        if marks and not base:
            indices.extend(range(offset, offset + len(cluster)))
        offset += len(cluster)
    return indices


def bidi_hygiene(text: str) -> dict[str, object]:
    """Inspect invisible Unicode controls without modifying the scored output."""

    bidi_positions = [i for i, ch in enumerate(text) if ch in BIDI_CONTROLS]
    bidi_mark_positions = [i for i, ch in enumerate(text) if ch in BIDI_MARKS]
    bidi_embedding_positions = [i for i, ch in enumerate(text) if ch in BIDI_EMBEDDINGS]
    bidi_override_positions = [i for i, ch in enumerate(text) if ch in BIDI_OVERRIDES]
    bidi_isolate_positions = [i for i, ch in enumerate(text) if ch in BIDI_ISOLATES]
    zero_width_positions = [i for i, ch in enumerate(text) if ch in ZERO_WIDTH_CONTROLS]
    replacement_positions = [i for i, ch in enumerate(text) if ch == "\ufffd"]
    private_use_positions = [i for i, ch in enumerate(text) if unicodedata.category(ch) == "Co"]

    embedding_depth = 0
    unbalanced_embeddings = 0
    isolate_depth = 0
    unbalanced_isolates = 0
    for ch in text:
        if ch in {"\u202a", "\u202b", "\u202d", "\u202e"}:
            embedding_depth += 1
        elif ch == "\u202c":
            if embedding_depth:
                embedding_depth -= 1
            else:
                unbalanced_embeddings += 1
        elif ch in {"\u2066", "\u2067", "\u2068"}:
            isolate_depth += 1
        elif ch == "\u2069":
            if isolate_depth:
                isolate_depth -= 1
            else:
                unbalanced_isolates += 1

    unbalanced_embeddings += embedding_depth
    unbalanced_isolates += isolate_depth

    names = Counter(unicodedata.name(ch, "<UNNAMED>") for ch in text if ch in BIDI_CONTROLS)
    return {
        "bidi_control_count": len(bidi_positions),
        "bidi_control_positions": bidi_positions,
        "bidi_mark_count": len(bidi_mark_positions),
        "bidi_mark_positions": bidi_mark_positions,
        "bidi_embedding_count": len(bidi_embedding_positions),
        "bidi_embedding_positions": bidi_embedding_positions,
        "bidi_override_count": len(bidi_override_positions),
        "bidi_override_positions": bidi_override_positions,
        "bidi_isolate_count": len(bidi_isolate_positions),
        "bidi_isolate_positions": bidi_isolate_positions,
        "unsafe_bidi_control_count": (
            len(bidi_embedding_positions) + len(bidi_override_positions)
        ),
        "bidi_control_names": dict(names),
        "unbalanced_embeddings": unbalanced_embeddings,
        "unbalanced_isolates": unbalanced_isolates,
        "zero_width_count": len(zero_width_positions),
        "zero_width_positions": zero_width_positions,
        "replacement_character_count": len(replacement_positions),
        "replacement_character_positions": replacement_positions,
        "private_use_count": len(private_use_positions),
        "private_use_positions": private_use_positions,
        "presentation_form_count": sum(
            1 for ch in text if ord(ch) in HEBREW_PRESENTATION_FORMS_RANGE
        ),
    }
