from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

from PIL import Image
import pytest

from hebocrbench.converters import ConversionContext
from hebocrbench.converters.pinkas_webdataset import (
    PINKAS_SOURCE_DATASET,
    PinkasWebDatasetPolicy,
    convert_pinkas_webdataset_tar,
)


def _image_bytes(value: int) -> bytes:
    stream = io.BytesIO()
    Image.new("L", (48, 16), value).save(stream, format="JPEG")
    return stream.getvalue()


def _metadata(
    item_id: str,
    image: bytes,
    *,
    source_dataset: str,
    source_page: str,
    source_line_id: str,
    text: str,
) -> dict[str, object]:
    return {
        "id": item_id,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "image_width": 48,
        "image_height": 16,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_dataset": source_dataset,
        "source_license": "cc-by-4.0" if source_dataset == PINKAS_SOURCE_DATASET else "mit",
        "rights_status": "explicit_open",
        "language": "he",
        "script": "Hebr",
        "curated_config": "historical_handwriting_lines",
        "curated_granularity": "line",
        "modality": "historical_hebrew_handwritten_line",
        "granularity": "line",
        "split": "test",
        "source_split": "test",
        "original_split": "test",
        "source_page": source_page,
        "source_image": source_page.replace(".xml", ".jpg"),
        "source_line_id": source_line_id,
        "source_url": "https://zenodo.org/records/3569694",
        "quality_tier": "A",
        "rtl_text_order": "logical_unicode",
        "source_raw_text_order": "logical_rtl",
    }


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def _mixed_fixture(path: Path) -> None:
    pinkas_a = _image_bytes(240)
    samaritan_a = _image_bytes(180)
    pinkas_b = _image_bytes(120)
    samples = [
        (
            "pinkas_a",
            pinkas_a,
            _metadata(
                "pinkas_a",
                pinkas_a,
                source_dataset=PINKAS_SOURCE_DATASET,
                source_page="Page 132_1.xml",
                source_line_id="l1",
                text="שורה ראשונה",
            ),
        ),
        (
            "samaritan_a",
            samaritan_a,
            _metadata(
                "samaritan_a",
                samaritan_a,
                source_dataset="johnlockejrr/samaritan_v1",
                source_page="samaritan-page",
                source_line_id="1",
                text="טקסט שלא ייכלל",
            ),
        ),
        (
            "pinkas_b",
            pinkas_b,
            _metadata(
                "pinkas_b",
                pinkas_b,
                source_dataset=PINKAS_SOURCE_DATASET,
                source_page="Page 132_1.xml",
                source_line_id="l2",
                text="שורה שנייה",
            ),
        ),
    ]
    with tarfile.open(path, "w") as archive:
        for item_id, image, metadata in samples:
            _add_bytes(archive, f"{item_id}.jpg", image)
            _add_bytes(
                archive,
                f"{item_id}.json",
                json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
            )


def _context() -> ConversionContext:
    return ConversionContext(
        source_id="historical-pinkas-handwriting-v1",
        source_version="locked-fixture",
        split="test",
        track="historical_pinkas_handwriting",
        license_expression="CC-BY-4.0",
        rights_uri="https://creativecommons.org/licenses/by/4.0/",
        redistribution="allowed",
        citation_key="pinkas-fixture",
        source_url="https://zenodo.org/records/3569694",
        metadata_defaults={
            "languages": ["he"],
            "script": "Hebr",
            "script_style": "historical_hebrew_handwriting",
            "era": "historical",
            "document_type": "communal_register_manuscript",
            "layout_type": "line",
            "vocalization": "none",
            "source_type": "real_manuscript_line_scan",
            "source_collection": "Pinkas historical handwriting",
        },
    )


def _policy(path: Path, *, expected_records: int = 2) -> PinkasWebDatasetPolicy:
    return PinkasWebDatasetPolicy(
        archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_page_counts=(("Page 132_1.xml", 2),),
        expected_records=expected_records,
    )


def test_locked_materializer_emits_only_pinkas_and_preserves_page_lineage(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    archive_path = source_root / "pinkas.tar"
    _mixed_fixture(archive_path)

    records = convert_pinkas_webdataset_tar(
        archive_path,
        source_root,
        tmp_path / "build",
        _context(),
        policy=_policy(archive_path),
    )

    assert len(records) == 2
    assert {record["metadata"]["source_dataset"] for record in records} == {PINKAS_SOURCE_DATASET}
    assert {record["metadata"]["source_item_id"] for record in records} == {
        "pinkas_a",
        "pinkas_b",
    }
    assert {record["document_id"] for record in records} == {
        "historical-pinkas-handwriting-v1-Page-132_1.xml"
    }
    assert all(
        record["metadata"]["benchmark_data_status"] == "real-public-fixed" for record in records
    )
    assert all(
        record["metadata"]["coverage_scope"] == "narrow-single-collection" for record in records
    )
    assert all(record["metadata"]["writer_disjoint"] is False for record in records)
    assert all((tmp_path / "build" / record["image"]["path"]).is_file() for record in records)
    assert len(list((tmp_path / "build" / "images").rglob("*.jpg"))) == 2


def test_locked_materializer_fails_closed_on_expected_count_drift(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    archive_path = source_root / "pinkas.tar"
    _mixed_fixture(archive_path)

    with pytest.raises(ValueError, match="subset count mismatch"):
        convert_pinkas_webdataset_tar(
            archive_path,
            source_root,
            tmp_path / "build",
            _context(),
            policy=_policy(archive_path, expected_records=3),
        )


def test_locked_materializer_rejects_archive_hash_drift_before_writing_images(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    archive_path = source_root / "pinkas.tar"
    _mixed_fixture(archive_path)
    policy = PinkasWebDatasetPolicy(
        archive_sha256="0" * 64,
        expected_page_counts=(("Page 132_1.xml", 2),),
        expected_records=2,
    )

    with pytest.raises(ValueError, match="TAR SHA-256 mismatch"):
        convert_pinkas_webdataset_tar(
            archive_path,
            source_root,
            tmp_path / "build",
            _context(),
            policy=policy,
        )

    assert not (tmp_path / "build").exists()


def test_locked_materializer_rejects_non_regular_tar_members(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    archive_path = source_root / "unsafe.tar"
    with tarfile.open(archive_path, "w") as archive:
        link = tarfile.TarInfo("unsafe-link.jpg")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)
    policy = PinkasWebDatasetPolicy(
        archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        expected_page_counts=(),
        expected_records=0,
    )

    with pytest.raises(ValueError, match="non-regular member"):
        convert_pinkas_webdataset_tar(
            archive_path,
            source_root,
            tmp_path / "build",
            _context(),
            policy=policy,
        )
