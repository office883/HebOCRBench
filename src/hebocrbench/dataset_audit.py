"""Leakage, provenance and content-integrity audit for built corpora."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence
import unicodedata

from rapidfuzz import fuzz, process


@dataclass(frozen=True, slots=True)
class AuditIssue:
    severity: str
    code: str
    message: str
    page_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class DatasetAudit:
    errors: list[AuditIssue] = field(default_factory=list)
    warnings: list[AuditIssue] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        page_ids: Iterable[str] = (),
    ) -> None:
        issue = AuditIssue(severity, code, message, tuple(sorted(set(page_ids))))
        (self.errors if severity == "error" else self.warnings).append(issue)

    def to_dict(self) -> dict[str, object]:
        def encode(issue: AuditIssue) -> dict[str, object]:
            return {
                "severity": issue.severity,
                "code": issue.code,
                "message": issue.message,
                "page_ids": list(issue.page_ids),
            }

        return {
            "is_valid": self.is_valid,
            "stats": self.stats,
            "errors": [encode(issue) for issue in self.errors],
            "warnings": [encode(issue) for issue in self.warnings],
        }


_REQUIRED_PROVENANCE = (
    "source_id",
    "source_version",
    "source_page_id",
    "source_url",
    "rights_uri",
    "redistribution",
    "citation_key",
    "license",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page_text(record: Mapping[str, object]) -> str:
    pieces: list[str] = []
    regions = record.get("regions", [])
    if isinstance(regions, list):
        for region in regions:
            if not isinstance(region, Mapping):
                continue
            lines = region.get("lines", [])
            if not isinstance(lines, list):
                continue
            for line in lines:
                if isinstance(line, Mapping) and isinstance(line.get("text"), str):
                    pieces.append(str(line["text"]))
    return "\n".join(pieces)


def _leakage_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = "".join(character for character in value if unicodedata.category(character) != "Cf")
    return re.sub(r"\s+", " ", value).strip()


def _cross_split_groups(
    records: Sequence[Mapping[str, object]],
    *,
    field_name: str,
    metadata: bool,
) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for record in records:
        source: object = record.get("metadata", {}) if metadata else record
        value = source.get(field_name) if isinstance(source, Mapping) else None
        if value in (None, ""):
            continue
        groups[str(value)].append((str(record.get("split", "")), str(record.get("page_id", ""))))
    return {
        value: pages
        for value, pages in groups.items()
        if len({split for split, _ in pages}) > 1
    }


def _add_group_leaks(
    report: DatasetAudit,
    records: Sequence[Mapping[str, object]],
    *,
    field_name: str,
    metadata: bool = False,
    severity: str = "error",
) -> None:
    for value, members in sorted(
        _cross_split_groups(records, field_name=field_name, metadata=metadata).items()
    ):
        report.add(
            severity,
            f"split_leak_{field_name}",
            f"{field_name}={value!r} occurs in multiple splits",
            (page_id for _, page_id in members),
        )


def audit_dataset(
    records: Sequence[Mapping[str, object]],
    dataset_root: str | Path,
    *,
    near_text_threshold: float = 0.94,
    minimum_near_text_length: int = 32,
) -> DatasetAudit:
    """Audit a built corpus without mutating it.

    Hard failures cover missing provenance, image integrity, and exact cross-split
    identity leaks. Text duplicates are warnings because repeated boilerplate may be
    legitimate, but they remain visible to release certification.
    """

    if not 0.0 <= near_text_threshold <= 1.0:
        raise ValueError("near_text_threshold must be between 0 and 1")
    root = Path(dataset_root)
    report = DatasetAudit()
    split_counts: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    image_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    text_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    normalized_texts: list[tuple[str, str, str]] = []

    seen_page_ids: set[str] = set()
    for record in records:
        page_id = str(record.get("page_id", ""))
        split = str(record.get("split", ""))
        split_counts[split] += 1
        if page_id in seen_page_ids:
            report.add("error", "duplicate_page_id", f"Duplicate page_id {page_id!r}", [page_id])
        seen_page_ids.add(page_id)

        metadata = record.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
            report.add("error", "invalid_provenance", "metadata must be an object", [page_id])
        source_counts[str(metadata.get("source_id", "<missing>"))] += 1
        for field_name in _REQUIRED_PROVENANCE:
            if metadata.get(field_name) in (None, ""):
                report.add(
                    "error",
                    f"missing_provenance_{field_name}",
                    f"Required provenance field {field_name!r} is missing",
                    [page_id],
                )

        image = record.get("image", {})
        if not isinstance(image, Mapping):
            report.add("error", "invalid_image_record", "image must be an object", [page_id])
        else:
            relative = str(image.get("path", ""))
            image_path = root / relative
            if not image_path.is_file():
                report.add("error", "missing_image", f"Image not found: {relative}", [page_id])
            else:
                actual = _sha256(image_path)
                declared = str(image.get("sha256", "")).lower()
                if not declared:
                    report.add("error", "missing_image_sha256", "Image SHA-256 is missing", [page_id])
                elif declared != actual:
                    report.add(
                        "error",
                        "image_sha256_mismatch",
                        f"Image hash mismatch for {relative}",
                        [page_id],
                    )
                image_groups[actual].append((split, page_id))

        normalized = _leakage_text(_page_text(record))
        # In classification tracks the repeated text is the class label itself,
        # not duplicated document content. Image hashes and writer/scribe groups
        # remain subject to the same hard cross-split leakage checks.
        is_classification_label = metadata.get("class_id") not in (None, "")
        if normalized and not is_classification_label:
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            text_groups[digest].append((split, page_id))
            normalized_texts.append((split, page_id, normalized))

    _add_group_leaks(report, records, field_name="document_id")
    _add_group_leaks(report, records, field_name="writer_id", metadata=True)
    _add_group_leaks(report, records, field_name="scribe_id", metadata=True)
    _add_group_leaks(report, records, field_name="source_page_id", metadata=True)
    # Reused form templates are suspicious but not universally invalid.
    _add_group_leaks(
        report,
        records,
        field_name="template_id",
        metadata=True,
        severity="warning",
    )

    for digest, members in sorted(image_groups.items()):
        if len({split for split, _ in members}) > 1:
            report.add(
                "error",
                "split_leak_image_hash",
                f"Identical image SHA-256 {digest} occurs in multiple splits",
                (page_id for _, page_id in members),
            )
    exact_pairs: set[frozenset[str]] = set()
    for digest, members in sorted(text_groups.items()):
        if len({split for split, _ in members}) > 1:
            page_ids = tuple(page_id for _, page_id in members)
            report.add(
                "warning",
                "split_duplicate_text",
                f"Identical normalized text SHA-256 {digest} occurs in multiple splits",
                page_ids,
            )
            for left in page_ids:
                for right in page_ids:
                    if left != right:
                        exact_pairs.add(frozenset((left, right)))

    # RapidFuzz evaluates the complete candidate matrix in compiled code. This
    # keeps the audit exact at the configured threshold without the quadratic
    # Python loop that made book-scale corpora impractically slow.
    eligible = [
        item for item in normalized_texts if len(item[2]) >= minimum_near_text_length
    ]
    if len(eligible) >= 2:
        similarities = process.cdist(
            [item[2] for item in eligible],
            [item[2] for item in eligible],
            scorer=fuzz.ratio,
            score_cutoff=near_text_threshold * 100.0,
            workers=-1,
        )
        for left_index, (left_split, left_id, left_text) in enumerate(eligible):
            for right_index in range(left_index + 1, len(eligible)):
                right_split, right_id, right_text = eligible[right_index]
                if left_split == right_split:
                    continue
                pair = frozenset((left_id, right_id))
                if pair in exact_pairs:
                    continue
                length_ratio = min(len(left_text), len(right_text)) / max(
                    len(left_text), len(right_text)
                )
                if length_ratio < near_text_threshold * 0.75:
                    continue
                similarity = float(similarities[left_index, right_index]) / 100.0
                if similarity >= near_text_threshold:
                    report.add(
                        "warning",
                        "split_near_duplicate_text",
                        f"Cross-split normalized text similarity is {similarity:.3f}",
                        [left_id, right_id],
                    )

    report.stats = {
        "pages": len(records),
        "splits": dict(sorted(split_counts.items())),
        "sources": dict(sorted(source_counts.items())),
        "errors": len(report.errors),
        "warnings": len(report.warnings),
    }
    return report
