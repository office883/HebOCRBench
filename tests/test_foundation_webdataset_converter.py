from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

from PIL import Image
import pytest
import yaml

from hebocrbench.converters import ConversionContext
from hebocrbench.converters.foundation_webdataset import (
    FOUNDATION_POLICIES,
    FoundationWebDatasetPolicy,
    convert_foundation_webdataset_tar,
)
from hebocrbench.corpus_builder import build_corpus
from hebocrbench.corpus_registry import load_registry
from hebocrbench.io import load_jsonl
from hebocrbench.unicode_utils import graphemes


def _image_bytes(value: int) -> bytes:
    stream = io.BytesIO()
    Image.new("L", (72, 24), value).save(stream, format="WEBP", lossless=True)
    return stream.getvalue()


def _metadata(
    item_id: str,
    text: str,
    *,
    profile: str,
    font_family: str,
    split: str = "test_synthetic",
    font_pool: str = "test_synthetic",
    font_sha256: str = "a" * 64,
) -> dict[str, object]:
    return {
        "id": item_id,
        "dataset": "Hebrew OCR Foundation",
        "version": "1.0.0",
        "split": split,
        "profile": profile,
        "text_logical": text,
        "normalization": "NFC",
        "base_direction": "rtl",
        "source_id": f"fixture:{item_id}",
        "seed": 20260725,
        "codepoints": len(text),
        "graphemes": len(graphemes(text)),
        "font": {
            "family": font_family,
            "style": "Regular",
            "basename": "fixture.ttf",
            "sha256": font_sha256,
            "pool": font_pool,
        },
        "render": {
            "engine": "Pillow-RAQM",
            "direction": "rtl",
            "language": "he",
            "font_px": 32,
        },
        "image": {"width": 72, "height": 24, "format": "webp"},
    }


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def _fixture(
    path: Path,
    *,
    profile: str = "niqqud",
    texts: tuple[str, ...] = ("שָׁלוֹם", "בְּדִיקָה"),
    font_family: str = "Noto Sans Hebrew",
    split: str = "test_synthetic",
    font_pool: str = "test_synthetic",
    item_prefix: str = "sample",
    font_sha256: str = "a" * 64,
) -> None:
    with tarfile.open(path, "w") as archive:
        for index, text in enumerate(texts):
            item_id = f"{item_prefix}_{index}"
            _add_bytes(archive, f"{item_id}.webp", _image_bytes(220 - index * 20))
            _add_bytes(
                archive,
                f"{item_id}.json",
                json.dumps(
                    _metadata(
                        item_id,
                        text,
                        profile=profile,
                        font_family=font_family,
                        split=split,
                        font_pool=font_pool,
                        font_sha256=font_sha256,
                    ),
                    ensure_ascii=False,
                ).encode("utf-8"),
            )


def _policy(
    path: Path,
    *,
    source_id: str = "biblical-niqqud-synthetic-diagnostic-v1",
    profile: str = "niqqud",
    texts: tuple[str, ...] = ("שָׁלוֹם", "בְּדִיקָה"),
    expected_font_family: str | None = None,
    train_path: Path | None = None,
    train_texts: tuple[str, ...] = (),
) -> FoundationWebDatasetPolicy:
    niqqud = 0
    cantillation = 0
    for text in texts:
        for character in text:
            codepoint = ord(character)
            if 0x0591 <= codepoint <= 0x05AF:
                cantillation += 1
            elif (0x05B0 <= codepoint <= 0x05BD) or codepoint in {
                0x05BF,
                0x05C1,
                0x05C2,
                0x05C4,
                0x05C5,
                0x05C7,
            }:
                niqqud += 1
    return FoundationWebDatasetPolicy(
        source_id=source_id,
        archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_archive_records=len(texts),
        expected_selected_records=len(texts),
        expected_profile=profile,
        expected_niqqud_marks=niqqud,
        expected_cantillation_marks=cantillation,
        expected_font_family=expected_font_family,
        train_archive_filename=train_path.name if train_path is not None else None,
        train_archive_sha256=(
            hashlib.sha256(train_path.read_bytes()).hexdigest() if train_path is not None else None
        ),
        expected_train_records=len(train_texts) if train_path is not None else None,
        expected_test_unique_source_ids=len(texts) if train_path is not None else None,
        expected_train_unique_source_ids=len(train_texts) if train_path is not None else None,
        expected_test_unique_texts=len(set(texts)) if train_path is not None else None,
        expected_train_unique_texts=(len(set(train_texts)) if train_path is not None else None),
        expected_test_unique_font_sha256=1 if train_path is not None else None,
        expected_train_unique_font_sha256=1 if train_path is not None else None,
        expected_font_family_overlap=1 if train_path is not None else None,
    )


def _context(
    *,
    source_id: str = "biblical-niqqud-synthetic-diagnostic-v1",
    track: str = "biblical_niqqud_synthetic_diagnostic",
) -> ConversionContext:
    return ConversionContext(
        source_id=source_id,
        source_version="locked-fixture",
        split="diagnostic",
        track=track,
        license_expression="LicenseRef-Synthetic-Source-Metadata",
        rights_uri="https://example.test/rights",
        redistribution="conditional",
        citation_key="foundation-fixture",
        source_url="https://example.test/foundation",
        metadata_defaults={
            "languages": ["he"],
            "script": "Hebr",
            "script_style": "synthetic_square_print",
            "era": "synthetic",
            "document_type": "synthetic_line",
            "layout_type": "line",
            "vocalization": "full",
            "cantillation_status": "absent",
            "source_type": "synthetic_rendered_line",
        },
    )


def test_niqqud_materializer_is_explicitly_synthetic_and_non_headline(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "train-niqqud-000.tar"
    _fixture(archive)

    records = convert_foundation_webdataset_tar(
        archive,
        source,
        tmp_path / "build",
        _context(),
        policy=_policy(archive),
    )

    assert len(records) == 2
    assert {record["split"] for record in records} == {"diagnostic"}
    assert {record["track"] for record in records} == {"biblical_niqqud_synthetic_diagnostic"}
    assert all(record["metadata"]["synthetic"] is True for record in records)
    assert all(record["metadata"]["headline_eligible"] is False for record in records)
    assert all(record["metadata"]["cantillation_status"] == "absent" for record in records)
    assert all(record["metadata"]["biblical_coverage_status"] == "unmet" for record in records)
    assert all((tmp_path / "build" / record["image"]["path"]).is_file() for record in records)


def test_rashi_materializer_locks_the_single_synthetic_font(tmp_path: Path):
    source_id = "rashi-print-synthetic-diagnostic-v1"
    texts = ("כתב רש״י סינתטי",)
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "train-rashi-000.tar"
    _fixture(
        archive,
        profile="rashi",
        texts=texts,
        font_family="Noto Rashi Hebrew",
    )
    policy = _policy(
        archive,
        source_id=source_id,
        profile="rashi",
        texts=texts,
        expected_font_family="Noto Rashi Hebrew",
    )

    records = convert_foundation_webdataset_tar(
        archive,
        source,
        tmp_path / "build",
        _context(source_id=source_id, track="rashi_print_synthetic_diagnostic"),
        policy=policy,
    )

    assert len(records) == 1
    metadata = records[0]["metadata"]
    assert metadata["font"]["family"] == "Noto Rashi Hebrew"
    assert metadata["coverage_scope"] == "synthetic-single-rashi-font"
    assert metadata["historical_scan_status"] == "absent"
    assert metadata["historical_coverage_status"] == "unmet"


def test_test_shard_is_verified_disjoint_from_train_ids_text_and_font_bytes(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "test-synthetic-mixed-000.tar"
    train = source / "train-niqqud-000.tar"
    test_texts = ("שָׁלוֹם", "בְּדִיקָה")
    train_texts = ("מִלָּה אַחֶרֶת", "נִסָּיוֹן שׁוֹנֶה")
    _fixture(archive, texts=test_texts)
    _fixture(
        train,
        texts=train_texts,
        split="train",
        font_pool="train",
        item_prefix="train",
        font_sha256="b" * 64,
    )
    policy = _policy(
        archive,
        texts=test_texts,
        train_path=train,
        train_texts=train_texts,
    )

    records = convert_foundation_webdataset_tar(
        archive,
        source,
        tmp_path / "build",
        _context(),
        policy=policy,
    )

    audit = records[0]["metadata"]["train_disjointness"]
    assert audit["audit_status"] == "verified"
    assert audit["item_id_overlap"] == 0
    assert audit["source_id_overlap"] == 0
    assert audit["text_overlap"] == 0
    assert audit["font_sha256_overlap"] == 0


def test_materializer_rejects_cantillation_when_locked_policy_says_absent(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "train-niqqud-000.tar"
    texts = ("בְּרֵאשִׁ֖ית",)
    _fixture(archive, texts=texts)
    policy = _policy(archive, texts=texts)
    policy = FoundationWebDatasetPolicy(
        source_id=policy.source_id,
        archive_sha256=policy.archive_sha256,
        expected_archive_records=policy.expected_archive_records,
        expected_selected_records=policy.expected_selected_records,
        expected_profile=policy.expected_profile,
        expected_niqqud_marks=policy.expected_niqqud_marks,
        expected_cantillation_marks=0,
    )

    with pytest.raises(ValueError, match="cantillation-mark count mismatch"):
        convert_foundation_webdataset_tar(
            archive,
            source,
            tmp_path / "build",
            _context(),
            policy=policy,
        )

    assert not (tmp_path / "build").exists()


def test_materializer_rejects_archive_hash_before_writing(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "train-niqqud-000.tar"
    _fixture(archive)
    policy = _policy(archive)
    policy = FoundationWebDatasetPolicy(
        source_id=policy.source_id,
        archive_sha256="0" * 64,
        expected_archive_records=policy.expected_archive_records,
        expected_selected_records=policy.expected_selected_records,
        expected_profile=policy.expected_profile,
        expected_niqqud_marks=policy.expected_niqqud_marks,
    )

    with pytest.raises(ValueError, match="TAR SHA-256 mismatch"):
        convert_foundation_webdataset_tar(
            archive,
            source,
            tmp_path / "build",
            _context(),
            policy=policy,
        )

    assert not (tmp_path / "build").exists()


def test_corpus_builder_dispatches_foundation_converter_as_diagnostic(tmp_path: Path):
    source_id = "biblical-niqqud-synthetic-diagnostic-v1"
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "train-niqqud-000.tar"
    _fixture(archive)
    fixture_policy = _policy(archive)
    original_policy = FOUNDATION_POLICIES[source_id]
    FOUNDATION_POLICIES[source_id] = fixture_policy
    try:
        registry_payload = {
            "schema_version": "1.0",
            "registry_version": "fixture",
            "benchmark": "HebOCRBench",
            "sources": {
                source_id: {
                    "title": "Synthetic niqqud diagnostic fixture",
                    "version": "fixture",
                    "task": "synthetic_niqqud_diagnostic",
                    "track": "biblical_niqqud_synthetic_diagnostic",
                    "languages": ["he"],
                    "script": "Hebr",
                    "status": "diagnostic",
                    "converter": "foundation-webdataset",
                    "homepage": "https://example.test/foundation",
                    "citation": {"key": "foundation-fixture", "text": "Fixture"},
                    "license": {
                        "spdx": "LicenseRef-Synthetic-Source-Metadata",
                        "tier": "external-review",
                        "redistribution": "conditional",
                        "requires_acceptance": False,
                    },
                    "artifacts": [],
                    "discovery": {
                        "annotation_globs": ["train-niqqud-000.tar"],
                        "image_roots": ["."],
                        "exclude_globs": [],
                        "split_from_path": False,
                    },
                    "split": {
                        "strategy": "fixed",
                        "group_fields": ["document_id"],
                        "ratios": {"diagnostic": 1.0},
                        "seed": 20260725,
                    },
                    "metadata": {
                        "script_style": "synthetic_square_print",
                        "era": "synthetic",
                        "document_type": "synthetic_line",
                        "layout_type": "line",
                        "vocalization": "full",
                        "cantillation_status": "absent",
                        "source_type": "synthetic_rendered_line",
                    },
                }
            },
        }
        registry_path = tmp_path / "registry.yaml"
        registry_path.write_text(
            yaml.safe_dump(registry_payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        registry = load_registry(registry_path)
        output = tmp_path / "output"

        result = build_corpus(
            registry,
            {source_id: source},
            output,
            source_ids={source_id},
            accepted_source_ids=set(),
            benchmark_version="1.0.0",
            profile=source_id,
            track_id=source_id,
            profile_scope="full",
        )
    finally:
        FOUNDATION_POLICIES[source_id] = original_policy

    assert result.page_count == 2
    assert result.manifest["track_id"] == source_id
    records = load_jsonl(output / "gold.jsonl")
    assert {record["split"] for record in records} == {"diagnostic"}
    assert all(record["metadata"]["rankable"] is False for record in records)
