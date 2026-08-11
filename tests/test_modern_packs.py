from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

import hebocrbench.modern_packs as modern_packs
from hebocrbench.io import load_jsonl, sha256_file, write_json, write_jsonl
from hebocrbench.modern_packs import (
    ModernPackError,
    build_modern_suite_packs,
    remap_pack_predictions,
    verify_modern_suite_packs,
)
from hebocrbench.modern_suite import DEFAULT_HEADLINE_TRACKS, build_modern_suite_lock


def _inventory(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.name != "manifest.json"
    ]


def _track_root(parent: Path, track_id: str) -> Path:
    root = parent / track_id
    image_path = root / "images" / f"original-{track_id}.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (32, 20), "white").save(image_path)
    page_id = f"original-page-{track_id}"
    document_id = f"original-document-{track_id}"
    record = {
        "schema_version": "1.0",
        "page_id": page_id,
        "document_id": document_id,
        "split": "test",
        "track": track_id.removesuffix("-v1").replace("-", "_"),
        "image": {
            "path": image_path.relative_to(root).as_posix(),
            "width": 32,
            "height": 20,
            "rotation_degrees": 0,
            "sha256": sha256_file(image_path),
        },
        "metadata": {
            "source_id": f"private-source-{track_id}",
            "source_url": f"https://example.invalid/{track_id}/{page_id}",
        },
        "regions": [
            {
                "region_id": f"original-region-{track_id}",
                # The real BiDi corpus legitimately uses the semantic value
                # "table" while one original table identifier is also
                # literally "table".  This must not be mistaken for an ID
                # disclosure in the participant pack.
                "type": "table" if track_id == "modern-bidi-v1" else "body",
                "polygon": [[0, 0], [31, 0], [31, 19], [0, 19]],
                "base_direction": "rtl",
                "language": "he",
                "reading_index": 0,
                "lines": [
                    {
                        "line_id": f"original-line-{track_id}",
                        "polygon": [[1, 1], [30, 1], [30, 18], [1, 18]],
                        "baseline": [[30, 17], [1, 17]],
                        "text": f"טקסט זהב סודי {track_id}",
                        "base_direction": "rtl",
                        "language": "he",
                    }
                ],
            }
        ],
        "reading_order": {"edges": []},
        "tables": [
            {
                "table_id": "table"
                if track_id == "modern-bidi-v1"
                else f"original-table-{track_id}",
                "region_id": f"original-region-{track_id}",
                "n_rows": 1,
                "n_cols": 1,
                "cells": [
                    {
                        "row_start": 0,
                        "row_end": 1,
                        "col_start": 0,
                        "col_end": 1,
                        "text": "תא סודי",
                    }
                ],
            }
        ],
        "form_fields": [
            {
                "field_id": f"original-field-{track_id}",
                "label_text": "תווית סודית",
                "value_text": "ערך סודי",
                "label_region_id": f"original-region-{track_id}",
                "value_region_id": f"original-region-{track_id}",
            }
        ],
    }
    records = [record]
    if track_id == "modern-page-ocr-v1":
        second = json.loads(json.dumps(record))
        second["page_id"] = f"original-page-2-{track_id}"
        second["document_id"] = f"original-document-2-{track_id}"
        second["regions"][0]["region_id"] = f"original-region-2-{track_id}"
        second["regions"][0]["lines"][0]["line_id"] = f"original-line-2-{track_id}"
        second["tables"][0]["table_id"] = f"original-table-2-{track_id}"
        second["tables"][0]["region_id"] = f"original-region-2-{track_id}"
        second["form_fields"][0]["field_id"] = f"original-field-2-{track_id}"
        second["form_fields"][0]["label_region_id"] = f"original-region-2-{track_id}"
        second["form_fields"][0]["value_region_id"] = f"original-region-2-{track_id}"
        records.append(second)
    write_jsonl(root / "gold.jsonl", records)
    write_json(root / "stats.json", {"pages": len(records)})
    write_json(root / "audit.json", {"is_valid": True})
    dataset_fingerprint = hashlib.sha256(f"dataset:{track_id}".encode()).hexdigest()
    write_json(
        root / "dataset.lock.json",
        {
            "schema_version": "1.0",
            "dataset_fingerprint": dataset_fingerprint,
            "records_sha256": sha256_file(root / "gold.jsonl"),
            "stats_sha256": sha256_file(root / "stats.json"),
        },
    )
    write_json(
        root / "certification.json",
        {
            "schema_version": "1.0",
            "track_id": track_id,
            "dataset_fingerprint": dataset_fingerprint,
            "valid": True,
        },
    )
    manifest = {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "benchmark_version": "1.0.0",
        "profile": "modern-hebrew-print-v1",
        "profile_scope": "track-component",
        "track_id": track_id,
        "dataset_fingerprint": dataset_fingerprint,
        "registry_fingerprint": "a" * 64,
        "page_count": len(records),
        "files": _inventory(root),
    }
    write_json(root / "manifest.json", manifest)
    write_json(
        root / "FROZEN.json",
        {
            "schema_version": "1.0",
            "benchmark_version": "1.0.0",
            "dataset_fingerprint": dataset_fingerprint,
            "manifest_sha256": sha256_file(root / "manifest.json"),
            "verified_files": len(manifest["files"]),
        },
    )
    write_json(
        root / "CERTIFIED.json",
        {
            "schema_version": "1.0",
            "certified": True,
            "benchmark_version": "1.0.0",
            "registry_fingerprint": "a" * 64,
            "dataset_fingerprint": dataset_fingerprint,
            "certification_sha256": sha256_file(root / "certification.json"),
        },
    )
    return root


def _fixture_suite(tmp_path: Path):
    roots = {
        track_id: _track_root(tmp_path / "roots", track_id) for track_id in DEFAULT_HEADLINE_TRACKS
    }
    suite = build_modern_suite_lock(
        roots,
        profile_id="modern-hebrew-print-v1",
        profile_fingerprint="b" * 64,
        registry_fingerprint="a" * 64,
    )
    return roots, suite


def _all_participant_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.suffix in {".json", ".jsonl", ".md", ".txt"}
    )


def _reseal_packs(result) -> None:
    modern_packs._write_lock(
        result.participant_root,
        role="participant",
        suite_fingerprint=result.suite_fingerprint,
        name="PACK-LOCK.json",
    )
    participant_fingerprint = json.loads(
        (result.participant_root / "PACK-LOCK.json").read_text(encoding="utf-8")
    )["pack_fingerprint"]
    participant_manifest = json.loads(
        (result.participant_root / "PACK-MANIFEST.json").read_text(encoding="utf-8")
    )
    organizer_manifest_path = result.organizer_root / "ORGANIZER-MANIFEST.json"
    organizer_manifest = json.loads(organizer_manifest_path.read_text(encoding="utf-8"))
    organizer_manifest["participant_pack_fingerprint"] = participant_fingerprint
    for track_id, participant_track in participant_manifest["tracks"].items():
        organizer_manifest["tracks"][track_id]["inputs_sha256"] = participant_track["inputs_sha256"]
    write_json(organizer_manifest_path, organizer_manifest)
    modern_packs._write_lock(
        result.organizer_root,
        role="organizer",
        suite_fingerprint=result.suite_fingerprint,
        name="ORGANIZER-LOCK.json",
    )


def _replace_and_reseal_organizer_map(result, track_id: str, mappings) -> None:
    map_path = result.organizer_root / "maps" / f"{track_id}.jsonl"
    write_jsonl(map_path, mappings)
    organizer_manifest_path = result.organizer_root / "ORGANIZER-MANIFEST.json"
    organizer_manifest = json.loads(organizer_manifest_path.read_text(encoding="utf-8"))
    organizer_manifest["tracks"][track_id]["map_sha256"] = sha256_file(map_path)
    write_json(organizer_manifest_path, organizer_manifest)
    modern_packs._write_lock(
        result.organizer_root,
        role="organizer",
        suite_fingerprint=result.suite_fingerprint,
        name="ORGANIZER-LOCK.json",
    )


def test_suite_pack_has_no_gold_original_ids_or_urls_and_remaps_for_original_gold(
    tmp_path: Path,
) -> None:
    roots, suite = _fixture_suite(tmp_path)
    original_gold = {
        track_id: (root / "gold.jsonl").read_bytes() for track_id, root in roots.items()
    }
    result = build_modern_suite_packs(
        suite,
        roots,
        tmp_path / "packs",
        id_key=b"deterministic-organizer-secret-key-0001",
    )
    report = verify_modern_suite_packs(
        result.participant_root,
        result.organizer_root,
        suite_lock=suite,
        track_roots=roots,
    )

    assert report.valid
    assert report.page_count == 6
    assert not list(result.participant_root.rglob("gold.jsonl"))
    participant_text = _all_participant_text(result.participant_root)
    assert "טקסט זהב סודי" not in participant_text
    assert "example.invalid" not in participant_text
    assert "original-page-" not in participant_text
    assert "original-document-" not in participant_text
    assert "original-region-" not in participant_text
    assert "original-line-" not in participant_text
    manifest = json.loads(
        (result.participant_root / "PACK-MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["image_sharding"] == {
        "scheme": "sha256-opaque-page-id-prefix-2hex-v1",
        "hash_algorithm": "sha256",
        "hash_input": "opaque-page-id-utf8",
        "prefix_hex_chars": 2,
        "max_files_per_directory": 10_000,
        "path_template": ("tracks/{track_id}/images/{shard}/{opaque_page_id}{source_suffix}"),
    }
    assert report.checks["two_hex_image_sharding"] is True
    for packed_track in DEFAULT_HEADLINE_TRACKS:
        packed_inputs = load_jsonl(
            result.participant_root / "tracks" / packed_track / "inputs.jsonl"
        )
        shard_counts: dict[str, int] = {}
        for item in packed_inputs:
            relative = Path(item["image"]["path"])
            expected_shard = hashlib.sha256(item["page_id"].encode("utf-8")).hexdigest()[:2]
            assert relative == (
                Path("tracks") / packed_track / "images" / expected_shard / f"{item['page_id']}.png"
            )
            shard_counts[expected_shard] = shard_counts.get(expected_shard, 0) + 1
        track_manifest = manifest["tracks"][packed_track]
        assert track_manifest["image_count"] == len(packed_inputs)
        assert track_manifest["image_shard_count"] == len(shard_counts)
        assert track_manifest["max_images_per_shard"] == max(shard_counts.values())

    track_id = "modern-line-recognition-v1"
    public_input = load_jsonl(result.participant_root / "tracks" / track_id / "inputs.jsonl")[0]
    public_region = public_input["regions"][0]
    public_line = public_region["lines"][0]
    predictions = [
        {
            "schema_version": "1.0",
            "track_id": track_id,
            "page_id": public_input["page_id"],
            "document_id": public_input["document_id"],
            "regions": [
                {
                    "region_id": public_region["region_id"],
                    "lines": [{"line_id": public_line["line_id"], "text": "תחזית מודל"}],
                }
            ],
        }
    ]
    remapped = remap_pack_predictions(
        predictions,
        result.organizer_root,
        track_id=track_id,
        output_path=tmp_path / "remapped.jsonl",
    )
    assert remapped[0]["page_id"] == f"original-page-{track_id}"
    assert remapped[0]["document_id"] == f"original-document-{track_id}"
    assert remapped[0]["regions"][0]["region_id"] == f"original-region-{track_id}"
    assert remapped[0]["regions"][0]["lines"][0]["line_id"] == f"original-line-{track_id}"
    assert load_jsonl(tmp_path / "remapped.jsonl") == remapped
    assert {
        track_id: (root / "gold.jsonl").read_bytes() for track_id, root in roots.items()
    } == original_gold


def test_suite_pack_is_deterministic_and_detects_byte_tampering(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    first = build_modern_suite_packs(
        suite, roots, tmp_path / "first", id_key=b"same-key-for-repeatable-suite-pack-001"
    )
    second = build_modern_suite_packs(
        suite, roots, tmp_path / "second", id_key=b"same-key-for-repeatable-suite-pack-001"
    )
    assert first.participant_fingerprint == second.participant_fingerprint
    assert first.organizer_fingerprint == second.organizer_fingerprint

    image = next(first.participant_root.rglob("*.png"))
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(ModernPackError, match="inventory mismatch"):
        verify_modern_suite_packs(first.participant_root, first.organizer_root)


def test_semantic_leakage_is_rejected_even_if_participant_lock_is_resealed(
    tmp_path: Path,
) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"leakage-check-organizer-secret-key-001"
    )
    track_id = "modern-page-ocr-v1"
    inputs_path = result.participant_root / "tracks" / track_id / "inputs.jsonl"
    inputs = load_jsonl(inputs_path)
    inputs[0]["page_text"] = "טקסט זהב סודי"
    write_jsonl(inputs_path, inputs)
    participant_manifest_path = result.participant_root / "PACK-MANIFEST.json"
    participant_manifest = json.loads(participant_manifest_path.read_text(encoding="utf-8"))
    participant_manifest["tracks"][track_id]["inputs_sha256"] = sha256_file(inputs_path)
    write_json(participant_manifest_path, participant_manifest)
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="gold text key leaked"):
        verify_modern_suite_packs(result.participant_root, result.organizer_root)


def test_verifier_rejects_resealed_noncanonical_image_shard(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"image-sharding-organizer-secret-key-001"
    )
    track_id = "modern-line-recognition-v1"
    inputs_path = result.participant_root / "tracks" / track_id / "inputs.jsonl"
    inputs = load_jsonl(inputs_path)
    original_relative = Path(inputs[0]["image"]["path"])
    wrong_shard = "00" if original_relative.parent.name != "00" else "01"
    altered_relative = original_relative.parent.parent / wrong_shard / original_relative.name
    altered_path = result.participant_root / altered_relative
    altered_path.parent.mkdir()
    (result.participant_root / original_relative).rename(altered_path)
    inputs[0]["image"]["path"] = altered_relative.as_posix()
    write_jsonl(inputs_path, inputs)
    participant_manifest_path = result.participant_root / "PACK-MANIFEST.json"
    participant_manifest = json.loads(participant_manifest_path.read_text(encoding="utf-8"))
    participant_manifest["tracks"][track_id]["inputs_sha256"] = sha256_file(inputs_path)
    write_json(participant_manifest_path, participant_manifest)
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="image path is not anonymous"):
        verify_modern_suite_packs(result.participant_root, result.organizer_root)


def test_verifier_rejects_resealed_image_under_unknown_track(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"unexpected-track-organizer-secret-key-01"
    )
    source = next(result.participant_root.rglob("*.png"))
    unexpected = result.participant_root / "tracks/unexpected-track/images/00/extra.png"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_bytes(source.read_bytes())
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="participant track file inventory"):
        verify_modern_suite_packs(result.participant_root, result.organizer_root)


def test_verifier_rejects_resealed_unsharded_root_image(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"root-extra-image-organizer-secret-key-001"
    )
    source = next(result.participant_root.rglob("*.png"))
    (result.participant_root / "unsharded-extra.png").write_bytes(source.read_bytes())
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="participant pack contains an unexpected file"):
        verify_modern_suite_packs(result.participant_root, result.organizer_root)


def test_verifier_rejects_participant_image_symlink(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"participant-symlink-organizer-secret-key-01"
    )
    track_id = "modern-tables-v1"
    participant_input = load_jsonl(result.participant_root / "tracks" / track_id / "inputs.jsonl")[
        0
    ]
    original_record = load_jsonl(roots[track_id] / "gold.jsonl")[0]
    packed_image = result.participant_root / participant_input["image"]["path"]
    source_image = roots[track_id] / original_record["image"]["path"]
    packed_image.unlink()
    packed_image.symlink_to(source_image.resolve())

    with pytest.raises(ModernPackError, match="pack contains a symbolic link"):
        verify_modern_suite_packs(result.participant_root, result.organizer_root)


def test_verifier_rejects_resealed_image_that_differs_from_certified_root(
    tmp_path: Path,
) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"root-image-binding-organizer-secret-key-01"
    )
    track_id = "modern-tables-v1"
    inputs_path = result.participant_root / "tracks" / track_id / "inputs.jsonl"
    inputs = load_jsonl(inputs_path)
    participant_image = result.participant_root / inputs[0]["image"]["path"]
    Image.new("RGB", (32, 20), "red").save(participant_image)
    inputs[0]["image"]["sha256"] = sha256_file(participant_image)
    write_jsonl(inputs_path, inputs)
    inputs_sha256 = sha256_file(inputs_path)

    participant_manifest_path = result.participant_root / "PACK-MANIFEST.json"
    participant_manifest = json.loads(participant_manifest_path.read_text(encoding="utf-8"))
    participant_manifest["tracks"][track_id]["inputs_sha256"] = inputs_sha256
    write_json(participant_manifest_path, participant_manifest)
    organizer_manifest_path = result.organizer_root / "ORGANIZER-MANIFEST.json"
    organizer_manifest = json.loads(organizer_manifest_path.read_text(encoding="utf-8"))
    organizer_manifest["tracks"][track_id]["inputs_sha256"] = inputs_sha256
    write_json(organizer_manifest_path, organizer_manifest)
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="image differs from certified root"):
        verify_modern_suite_packs(
            result.participant_root,
            result.organizer_root,
            suite_lock=suite,
            track_roots=roots,
        )


def test_remap_rejects_resealed_nested_hmac_forgery(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"nested-hmac-forgery-organizer-key-00001"
    )
    track_id = "modern-line-recognition-v1"
    mappings = load_jsonl(result.organizer_root / "maps" / f"{track_id}.jsonl")
    public_region = next(iter(mappings[0]["identifiers"]["region"]))
    mappings[0]["identifiers"]["region"][public_region] = "forged-original-region"
    _replace_and_reseal_organizer_map(result, track_id, mappings)
    participant_input = load_jsonl(result.participant_root / "tracks" / track_id / "inputs.jsonl")[
        0
    ]

    with pytest.raises(ModernPackError, match="not HMAC-derived"):
        remap_pack_predictions(
            [{"page_id": participant_input["page_id"]}],
            result.organizer_root,
            track_id=track_id,
        )


def test_missing_oracle_alias_is_rejected_by_verifier_and_remap(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"missing-oracle-alias-organizer-key-0001"
    )
    track_id = "modern-line-recognition-v1"
    mappings = load_jsonl(result.organizer_root / "maps" / f"{track_id}.jsonl")
    mappings[0]["identifiers"]["region"].popitem()
    _replace_and_reseal_organizer_map(result, track_id, mappings)
    participant_input = load_jsonl(result.participant_root / "tracks" / track_id / "inputs.jsonl")[
        0
    ]

    with pytest.raises(ModernPackError, match="omits a participant region identifier"):
        verify_modern_suite_packs(
            result.participant_root,
            result.organizer_root,
            suite_lock=suite,
            track_roots=roots,
        )
    with pytest.raises(ModernPackError, match="region ID is not in the organizer map"):
        remap_pack_predictions(
            [
                {
                    "page_id": participant_input["page_id"],
                    "regions": [{"region_id": participant_input["regions"][0]["region_id"]}],
                }
            ],
            result.organizer_root,
            track_id=track_id,
        )


def test_swapped_document_alias_is_rejected_by_verifier_and_remap(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"swapped-document-organizer-secret-key-001"
    )
    track_id = "modern-page-ocr-v1"
    inputs_path = result.participant_root / "tracks" / track_id / "inputs.jsonl"
    inputs = load_jsonl(inputs_path)
    inputs[0]["document_id"], inputs[1]["document_id"] = (
        inputs[1]["document_id"],
        inputs[0]["document_id"],
    )
    write_jsonl(inputs_path, inputs)
    participant_manifest_path = result.participant_root / "PACK-MANIFEST.json"
    participant_manifest = json.loads(participant_manifest_path.read_text(encoding="utf-8"))
    participant_manifest["tracks"][track_id]["inputs_sha256"] = sha256_file(inputs_path)
    write_json(participant_manifest_path, participant_manifest)
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="document identifier differs"):
        verify_modern_suite_packs(
            result.participant_root,
            result.organizer_root,
            suite_lock=suite,
            track_roots=roots,
        )
    with pytest.raises(ModernPackError, match="prediction document ID differs"):
        remap_pack_predictions(
            [{"page_id": inputs[0]["page_id"], "document_id": inputs[0]["document_id"]}],
            result.organizer_root,
            track_id=track_id,
            require_complete=False,
        )


def test_remap_rejects_resealed_truncated_organizer_map(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"truncated-map-organizer-secret-key-00001"
    )
    track_id = "modern-page-ocr-v1"
    mappings = load_jsonl(result.organizer_root / "maps" / f"{track_id}.jsonl")
    mappings.pop()
    _replace_and_reseal_organizer_map(result, track_id, mappings)

    with pytest.raises(ModernPackError, match="organizer map page count is stale"):
        remap_pack_predictions(
            [{"page_id": mappings[0]["public_page_id"]}],
            result.organizer_root,
            track_id=track_id,
        )


def test_verifier_rejects_resealed_oracle_geometry_change(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"oracle-geometry-organizer-secret-key-0001"
    )
    track_id = "modern-line-recognition-v1"
    inputs_path = result.participant_root / "tracks" / track_id / "inputs.jsonl"
    inputs = load_jsonl(inputs_path)
    inputs[0]["regions"][0]["polygon"][0] = [7, 7]
    write_jsonl(inputs_path, inputs)
    participant_manifest_path = result.participant_root / "PACK-MANIFEST.json"
    participant_manifest = json.loads(participant_manifest_path.read_text(encoding="utf-8"))
    participant_manifest["tracks"][track_id]["inputs_sha256"] = sha256_file(inputs_path)
    write_json(participant_manifest_path, participant_manifest)
    _reseal_packs(result)

    with pytest.raises(ModernPackError, match="oracle layout differs"):
        verify_modern_suite_packs(
            result.participant_root,
            result.organizer_root,
            suite_lock=suite,
            track_roots=roots,
        )


def test_failed_overwrite_preserves_existing_pack_directory(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep-me.txt"
    sentinel.write_text("old complete pack\n", encoding="utf-8")
    broken_gold = roots["modern-page-ocr-v1"] / "gold.jsonl"
    broken_gold.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ModernPackError, match=r"manifest (?:size|hash) is stale"):
        build_modern_suite_packs(
            suite,
            roots,
            output,
            id_key=b"atomic-overwrite-organizer-key-000001",
            overwrite=True,
        )
    assert sentinel.read_text(encoding="utf-8") == "old complete pack\n"


def test_remap_rejects_unknown_duplicate_and_incomplete_predictions(tmp_path: Path) -> None:
    roots, suite = _fixture_suite(tmp_path)
    result = build_modern_suite_packs(
        suite, roots, tmp_path / "packs", id_key=b"submission-validation-secret-key-001"
    )
    track_id = "modern-tables-v1"
    public = load_jsonl(result.participant_root / "tracks" / track_id / "inputs.jsonl")[0]

    with pytest.raises(ModernPackError, match="not in the organizer map"):
        remap_pack_predictions(
            [{"page_id": "hbo-v1-p-000000000000000000000000"}],
            result.organizer_root,
            track_id=track_id,
        )
    with pytest.raises(ModernPackError, match="duplicate prediction"):
        remap_pack_predictions(
            [{"page_id": public["page_id"]}, {"page_id": public["page_id"]}],
            result.organizer_root,
            track_id=track_id,
        )
    page_track = "modern-page-ocr-v1"
    first_page = load_jsonl(result.participant_root / "tracks" / page_track / "inputs.jsonl")[0]
    with pytest.raises(ModernPackError, match="incomplete"):
        remap_pack_predictions(
            [{"page_id": first_page["page_id"]}],
            result.organizer_root,
            track_id=page_track,
        )
