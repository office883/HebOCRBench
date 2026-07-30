"""Safe import policy for already-canonical HebOCRBench gold records."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import ModuleType

from .io import load_jsonl


def install(engine: ModuleType) -> None:
    """Install prebuilt-gold support into the stable corpus-builder engine."""

    original_convert_source = engine._convert_source

    def convert_prebuilt_gold(source, annotation: Path, source_root: Path, build_root: Path):
        raw_records = load_jsonl(annotation)
        if not raw_records:
            raise engine.BuildError(f"{source.source_id}: prebuilt gold is empty: {annotation}")
        relative_annotation = annotation.relative_to(source_root)
        split = engine._upstream_split(source, relative_annotation)
        context = engine._context(source, split)
        source_base = source_root.resolve()
        provenance_keys = {
            "source_id",
            "source_version",
            "source_annotation_path",
            "source_url",
            "citation_key",
            "license",
            "rights_uri",
            "redistribution",
        }
        records: list[dict[str, object]] = []
        for index, raw_record in enumerate(raw_records, start=1):
            if not isinstance(raw_record, Mapping):
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt record is not an object"
                )
            record = deepcopy(dict(raw_record))
            page_id = str(record.get("page_id", "")).strip()
            if not page_id:
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt record has no page_id"
                )
            image = record.get("image")
            if not isinstance(image, dict) or not isinstance(image.get("path"), str):
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt record has no image path"
                )
            raw_image_path = Path(str(image["path"]))
            if raw_image_path.is_absolute():
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt image path must be relative"
                )
            image_path = (source_base / raw_image_path).resolve()
            if image_path != source_base and source_base not in image_path.parents:
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt image path escapes source root"
                )
            if not image_path.is_file():
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt image does not exist: {raw_image_path}"
                )
            actual_digest = engine.sha256_file(image_path)
            declared_digest = str(image.get("sha256", "")).lower().strip()
            if (
                declared_digest
                and declared_digest != "0" * 64
                and declared_digest != actual_digest
            ):
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt image SHA-256 mismatch: "
                    f"expected {declared_digest}, got {actual_digest}"
                )
            relative_image, digest = engine._copy_image(
                image_path, build_root, source.source_id
            )
            image["path"] = relative_image
            image["sha256"] = digest

            existing_metadata = record.get("metadata", {})
            if not isinstance(existing_metadata, Mapping):
                raise engine.BuildError(
                    f"{annotation}:{index}: prebuilt metadata is not an object"
                )
            annotation_key = f"{relative_annotation.as_posix()}#page_id={page_id}"
            canonical_metadata = context.metadata(annotation_path=annotation_key)
            merged_metadata = dict(canonical_metadata)
            merged_metadata.update(dict(existing_metadata))
            for key in provenance_keys:
                merged_metadata[key] = canonical_metadata[key]
            merged_metadata["source_image_path"] = raw_image_path.as_posix()
            merged_metadata["source_page_id"] = page_id
            merged_metadata["document_id_method"] = "prebuilt_gold_record_v1"
            record["metadata"] = merged_metadata
            record["split"] = split
            records.append(record)
        return records

    def convert_source(source, source_root: Path, build_root: Path):
        if source.converter != "none":
            return original_convert_source(source, source_root, build_root)
        annotations = engine._discover_annotations(source, source_root)
        if not annotations:
            raise engine.BuildError(
                f"{source.source_id}: no annotations matched the registry discovery rules"
            )
        records: list[dict[str, object]] = []
        for annotation in annotations:
            records.extend(
                convert_prebuilt_gold(source, annotation, source_root, build_root)
            )
        try:
            return engine.assign_splits(records, source.split)
        except engine.SplitPolicyError as exc:
            raise engine.BuildError(
                f"{source.source_id}: split assignment failed: {exc}"
            ) from exc

    engine._convert_prebuilt_gold = convert_prebuilt_gold
    engine._convert_source = convert_source
