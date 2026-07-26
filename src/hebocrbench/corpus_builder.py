"""Atomic, deterministic construction of federated HebOCRBench datasets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Mapping, Sequence
import xml.etree.ElementTree as ET

from .converters import ConversionContext
from .converters.alto import convert_alto_file
from .converters.pagexml import convert_pagexml_file
from .converters.hf_image_text import convert_hf_image_text_manifest
from .converters.modern_pdf import ModernPdfError, convert_modern_pdf_manifest
from .corpus_registry import CorpusRegistry, CorpusSource
from .corpus_stats import compute_corpus_stats
from .dataset_audit import DatasetAudit, audit_dataset
from .io import sha256_file, write_json, write_jsonl
from .splitting import SplitPolicyError, assign_splits
from .validator import validate_gold_records


class BuildError(RuntimeError):
    """A corpus cannot be built without violating the benchmark contract."""


@dataclass(frozen=True, slots=True)
class CorpusBuildResult:
    output_root: Path
    page_count: int
    dataset_fingerprint: str
    manifest: Mapping[str, object]
    audit: DatasetAudit


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _matches_any(path: Path, patterns: Sequence[str], *, relative_to: Path) -> bool:
    relative = path.relative_to(relative_to)
    return any(relative.match(pattern) for pattern in patterns)


def _discover_annotations(source: CorpusSource, root: Path) -> list[Path]:
    raw_globs = source.discovery.get("annotation_globs", [])
    if not isinstance(raw_globs, list):
        raise BuildError(f"{source.source_id}: annotation_globs must be a list")
    excluded = source.discovery.get("exclude_globs", [])
    if not isinstance(excluded, list):
        excluded = []
    found: set[Path] = set()
    for pattern in raw_globs:
        for path in root.glob(str(pattern)):
            if path.is_file() and not _matches_any(path, [str(item) for item in excluded], relative_to=root):
                found.add(path)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _referenced_image(annotation: Path, converter: str) -> str:
    root = ET.parse(annotation).getroot()
    if converter == "pagexml":
        page = next((node for node in root.iter() if _local_name(node.tag) == "Page"), None)
        value = page.get("imageFilename") if page is not None else None
    elif converter == "alto":
        node = next((item for item in root.iter() if _local_name(item.tag) == "fileName"), None)
        value = (node.text or "").strip() if node is not None else None
    else:
        raise BuildError(f"Unsupported converter {converter!r}")
    if not value:
        raise BuildError(f"{annotation}: annotation does not identify its source image")
    return value


def _find_image(annotation: Path, source_root: Path, source: CorpusSource) -> tuple[Path, Path]:
    image_name = _referenced_image(annotation, source.converter)
    candidates: list[tuple[Path, Path]] = [(annotation.parent / image_name, annotation.parent)]
    raw_roots = source.discovery.get("image_roots", ["."])
    if not isinstance(raw_roots, list):
        raw_roots = ["."]
    for root_value in raw_roots:
        image_root = source_root / str(root_value)
        candidates.append((image_root / image_name, image_root))
    seen: set[Path] = set()
    for candidate, image_root in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate, image_root.resolve()

    matches: list[Path] = []
    basename = Path(image_name).name
    for root_value in raw_roots:
        image_root = source_root / str(root_value)
        if image_root.is_dir():
            matches.extend(path.resolve() for path in image_root.rglob(basename) if path.is_file())
    matches = sorted(set(matches))
    if len(matches) == 1:
        return matches[0], matches[0].parent
    if not matches:
        raise BuildError(f"{annotation}: referenced image {image_name!r} was not found")
    raise BuildError(f"{annotation}: referenced image {image_name!r} is ambiguous ({len(matches)} matches)")


def _upstream_split(source: CorpusSource, relative_annotation: Path) -> str:
    strategy = str(source.split.get("strategy", "hash_group"))
    if strategy in {"fixed", "diagnostic"}:
        ratios = source.split.get("ratios", {})
        if isinstance(ratios, Mapping) and len(ratios) == 1:
            return str(next(iter(ratios)))
        return "diagnostic"
    if strategy not in {"upstream", "official"}:
        return "train"
    mapping = source.split.get("upstream_map", {})
    if not isinstance(mapping, Mapping) or not mapping:
        raise BuildError(f"{source.source_id}: upstream split requires upstream_map")
    for part in relative_annotation.parts:
        if part in mapping:
            return str(mapping[part])
    raise BuildError(
        f"{source.source_id}: cannot map {relative_annotation.as_posix()} to an upstream split"
    )


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return token or "item"


def _identity(source: CorpusSource, relative_annotation: Path, image_stem: str) -> tuple[str, str]:
    relative = relative_annotation.as_posix()
    short = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
    page_id = f"{source.source_id}--{short}--{_safe_token(image_stem)}"
    parts = list(relative_annotation.with_suffix("").parts)
    upstream = source.split.get("upstream_map", {})
    if isinstance(upstream, Mapping) and parts and parts[0] in upstream:
        parts = parts[1:]
    if len(parts) > 1:
        document_key = "/".join(parts[:-1])
    else:
        # Conservative default: every annotation is a separate document unless
        # the registry supplies a path grouping. This prevents accidental leaks.
        document_key = parts[0] if parts else image_stem
    document_id = f"{source.source_id}--{_safe_token(document_key)}"
    return page_id, document_id


def _copy_image(image: Path, destination_root: Path, source_id: str) -> tuple[str, str]:
    digest = sha256_file(image)
    suffix = image.suffix.lower() or ".img"
    relative = Path("images") / source_id / f"{digest[:20]}-{_safe_token(image.stem)}{suffix}"
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != digest:
            raise BuildError(f"Image destination collision: {relative}")
    else:
        shutil.copyfile(image, destination)
    return relative.as_posix(), digest


def _context(source: CorpusSource, split: str) -> ConversionContext:
    defaults = dict(source.metadata)
    defaults.setdefault("languages", list(source.languages))
    defaults.setdefault("script", source.script)
    defaults.setdefault("source_collection", source.title)
    return ConversionContext(
        source_id=source.source_id,
        source_version=source.version,
        split=split,
        track=source.track,
        license_expression=source.license.spdx,
        rights_uri=source.license.uri or source.homepage,
        redistribution=source.license.redistribution,
        citation_key=str(source.citation.get("key", source.source_id)),
        source_url=source.homepage,
        metadata_defaults=defaults,
    )


def _convert_source(source: CorpusSource, source_root: Path, build_root: Path) -> list[dict[str, object]]:
    annotations = _discover_annotations(source, source_root)
    if not annotations:
        raise BuildError(f"{source.source_id}: no annotations matched the registry discovery rules")
    supported = {"pagexml", "alto", "image-text", "hf-image-text", "modern-pdf"}
    if source.converter not in supported:
        raise BuildError(
            f"{source.source_id}: converter {source.converter!r} is not a page-OCR v1 converter"
        )
    records: list[dict[str, object]] = []
    for annotation in annotations:
        relative_annotation = annotation.relative_to(source_root)
        split = _upstream_split(source, relative_annotation)
        context = _context(source, split)
        try:
            if source.converter == "modern-pdf":
                converted = convert_modern_pdf_manifest(
                    annotation, source_root, build_root, context
                )
                for record in converted:
                    image = record.get("image")
                    if not isinstance(image, dict) or not isinstance(image.get("path"), str):
                        raise ValueError("modern PDF converter returned no image path")
                    image_path = (build_root / str(image["path"])).resolve()
                    if build_root.resolve() != image_path and build_root.resolve() not in image_path.parents:
                        raise ValueError("modern PDF image path escapes build root")
                    if not image_path.is_file():
                        raise ValueError(f"modern PDF converter did not create image: {image['path']}")
                    image["sha256"] = sha256_file(image_path)
                    metadata = record.get("metadata")
                    if not isinstance(metadata, dict):
                        raise ValueError("modern PDF converter returned no metadata")
                    metadata.setdefault("source_annotation_path", relative_annotation.as_posix())
                    metadata.setdefault("source_page_id", relative_annotation.as_posix())
                    metadata.setdefault("document_id_method", "locked_modern_pdf_manifest_v1")
                    records.append(record)
                continue
            if source.converter in {"pagexml", "alto"}:
                image_path, image_root = _find_image(annotation, source_root, source)
                converter = convert_pagexml_file if source.converter == "pagexml" else convert_alto_file
                record = converter(annotation, image_root, context)
                page_id, document_id = _identity(source, relative_annotation, image_path.stem)
                record["page_id"] = page_id
                record["document_id"] = document_id
                document_id_method = "relative_annotation_parent_or_stem_v1"
            else:
                record = convert_hf_image_text_manifest(annotation, source_root, context)
                image = record.get("image")
                if not isinstance(image, Mapping) or not isinstance(image.get("path"), str):
                    raise ValueError("manifest converter returned no image path")
                image_path = (source_root / str(image["path"])).resolve()
                if source_root.resolve() not in image_path.parents:
                    raise ValueError("manifest image path escapes source root")
                document_id_method = "immutable_manifest_document_id_v1"
        except (OSError, ValueError, ModernPdfError, ET.ParseError, json.JSONDecodeError) as exc:
            raise BuildError(f"Cannot convert {annotation}: {exc}") from exc
        relative_image, digest = _copy_image(image_path, build_root, source.source_id)
        image = record["image"]
        assert isinstance(image, dict)
        image["path"] = relative_image
        image["sha256"] = digest
        metadata = record["metadata"]
        assert isinstance(metadata, dict)
        metadata["source_annotation_path"] = relative_annotation.as_posix()
        metadata.setdefault("source_page_id", relative_annotation.as_posix())
        metadata["source_image_path"] = image_path.relative_to(source_root).as_posix()
        metadata["document_id_method"] = document_id_method
        records.append(record)
    try:
        return assign_splits(records, source.split)
    except SplitPolicyError as exc:
        raise BuildError(f"{source.source_id}: split assignment failed: {exc}") from exc


def _copy_source_verification_report(
    source: CorpusSource,
    source_root: Path,
    build_root: Path,
) -> dict[str, object]:
    """Validate and preserve acquisition evidence without trusting it blindly."""

    candidates = [source_root / ".hebocrbench-source.json"]
    candidates.extend(parent / ".hebocrbench-source.json" for parent in list(source_root.parents)[:4])
    marker_path: Path | None = None
    marker: Mapping[str, object] | None = None
    problems: list[str] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        marker_path = candidate
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"invalid acquisition marker: {exc}")
            break
        if not isinstance(value, Mapping):
            problems.append("acquisition marker is not a JSON object")
            break
        marker = value
        break

    expected = {artifact.artifact_id: artifact for artifact in source.artifacts if artifact.required}
    sanitized_artifacts: list[dict[str, object]] = []
    if marker is None:
        if not problems:
            problems.append("no .hebocrbench-source.json acquisition marker was supplied")
    else:
        if marker.get("source_id") != source.source_id:
            problems.append(f"acquisition marker belongs to {marker.get('source_id')!r}")
        if marker.get("verification_status") != "verified":
            problems.append("acquisition marker status is not verified")
        raw = marker.get("artifacts", [])
        raw_items = raw if isinstance(raw, list) else []
        by_id = {
            str(item.get("artifact_id")): item
            for item in raw_items
            if isinstance(item, Mapping) and item.get("artifact_id")
        }
        for artifact_id, artifact in sorted(expected.items()):
            evidence = by_id.get(artifact_id)
            if evidence is None:
                problems.append(f"missing acquisition evidence for {artifact_id}")
                continue
            checksum = evidence.get("registry_checksum")
            if artifact.checksum is not None:
                wanted = {"algorithm": artifact.checksum.algorithm, "value": artifact.checksum.value}
                if checksum != wanted:
                    problems.append(f"registry checksum mismatch for {artifact_id}")
            if artifact.revision is not None and evidence.get("requested_revision") != artifact.revision:
                problems.append(f"requested revision mismatch for {artifact_id}")
            actual_sha = str(evidence.get("actual_sha256", "")).lower()
            if len(actual_sha) != 64 or any(character not in "0123456789abcdef" for character in actual_sha):
                problems.append(f"invalid actual SHA-256 for {artifact_id}")
            size = evidence.get("size_bytes")
            if not isinstance(size, int) or size < 0:
                problems.append(f"invalid size for {artifact_id}")
            sanitized_artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "requested_revision": evidence.get("requested_revision"),
                    "registry_checksum": checksum,
                    "actual_sha256": actual_sha,
                    "size_bytes": size,
                }
            )

    report: dict[str, object] = {
        "schema_version": "1.0",
        "source_id": source.source_id,
        "source_version": source.version,
        "verification_status": "verified_acquisition" if marker is not None and not problems else "unverified",
        "required_artifact_ids": sorted(expected),
        "artifacts": sanitized_artifacts,
        "problems": problems,
    }
    if marker_path is not None:
        report["verification_manifest_sha256"] = sha256_file(marker_path)
    destination = build_root / "source_reports" / f"{source.source_id}.json"
    write_json(destination, report)
    return report

def _attribution(source: CorpusSource) -> dict[str, object]:
    return {
        "source_id": source.source_id,
        "title": source.title,
        "version": source.version,
        "homepage": source.homepage,
        "citation": dict(source.citation),
        "license": {
            "spdx": source.license.spdx,
            "uri": source.license.uri,
            "tier": source.license.tier,
            "redistribution": source.license.redistribution,
            "requires_acceptance": source.license.requires_acceptance,
            "authority": source.license.authority,
            "conflicts": list(source.license.conflicts),
        },
    }


def _bibtex(sources: Sequence[CorpusSource]) -> str:
    entries: list[str] = []
    for source in sources:
        key = _safe_token(str(source.citation.get("key", source.source_id)))
        text = str(source.citation.get("text", source.title)).replace("{", "\\{").replace("}", "\\}")
        title = source.title.replace("{", "\\{").replace("}", "\\}")
        entries.append(
            "\n".join(
                [
                    f"@misc{{{key},",
                    f"  title = {{{title}}},",
                    f"  howpublished = {{\\url{{{source.homepage}}}}},",
                    f"  note = {{{text}}},",
                    f"  year = {{{source.version}}}",
                    "}",
                ]
            )
        )
    return "\n\n".join(entries) + "\n"


def _license_text(source: CorpusSource) -> str:
    lines = [
        f"Source: {source.title}",
        f"Source ID: {source.source_id}",
        f"Version: {source.version}",
        f"License: {source.license.spdx}",
        f"License URI: {source.license.uri or '<not supplied>'}",
        f"Authority: {source.license.authority or '<not supplied>'}",
        f"Redistribution: {source.license.redistribution}",
        f"Homepage: {source.homepage}",
        "",
        "Citation:",
        str(source.citation.get("text", source.title)),
    ]
    if source.license.conflicts:
        lines.extend(["", "Known license conflicts:", *[f"- {item}" for item in source.license.conflicts]])
    return "\n".join(lines) + "\n"


def _file_inventory(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, object]]:
    excluded = exclude or set()
    files: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        files.append({"path": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    return files


def build_corpus(
    registry: CorpusRegistry,
    source_roots: Mapping[str, str | Path],
    output_root: str | Path,
    *,
    source_ids: set[str] | frozenset[str] | None,
    accepted_source_ids: set[str] | frozenset[str],
    benchmark_version: str,
    profile: str,
    overwrite: bool = False,
) -> CorpusBuildResult:
    output = Path(output_root)
    selected = registry.select(source_ids=source_ids)
    if not selected:
        raise BuildError("No sources selected")
    missing_acceptance = [
        source.source_id
        for source in selected
        if source.license.requires_acceptance and source.source_id not in accepted_source_ids
    ]
    if missing_acceptance:
        raise BuildError("Explicit license acceptance required for: " + ", ".join(missing_acceptance))
    missing_roots = [source.source_id for source in selected if source.source_id not in source_roots]
    if missing_roots:
        raise BuildError("Missing source roots for: " + ", ".join(missing_roots))
    if output.exists() and not overwrite:
        raise BuildError(f"Output already exists: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.build-", dir=output.parent))
    try:
        records: list[dict[str, object]] = []
        source_verification: dict[str, object] = {}
        for source in selected:
            root = Path(source_roots[source.source_id]).resolve()
            if not root.is_dir():
                raise BuildError(f"Source root is not a directory: {source.source_id}={root}")
            source_verification[source.source_id] = _copy_source_verification_report(
                source, root, temporary
            )
            records.extend(_convert_source(source, root, temporary))
        records.sort(key=lambda record: str(record["page_id"]))

        validation = validate_gold_records(records, dataset_root=temporary)
        if not validation.is_valid:
            raise BuildError(
                "Built records failed validation: "
                + "; ".join(f"{issue.code}: {issue.message}" for issue in validation.errors[:10])
            )
        audit = audit_dataset(records, temporary)
        if not audit.is_valid:
            raise BuildError(
                "Built records failed leakage/integrity audit: "
                + "; ".join(f"{issue.code}: {issue.message}" for issue in audit.errors[:10])
            )

        write_jsonl(temporary / "gold.jsonl", records)
        stats = compute_corpus_stats(records)
        write_json(temporary / "stats.json", stats)
        write_json(temporary / "audit.json", audit.to_dict())
        attributions = [_attribution(source) for source in selected]
        write_jsonl(temporary / "attribution.jsonl", attributions)
        (temporary / "citations.bib").write_text(_bibtex(selected), encoding="utf-8", newline="\n")
        license_root = temporary / "licenses"
        license_root.mkdir(parents=True, exist_ok=True)
        for source in selected:
            (license_root / f"{source.source_id}.txt").write_text(
                _license_text(source), encoding="utf-8", newline="\n"
            )

        fingerprint_basis = {
            "benchmark": registry.benchmark,
            "benchmark_version": benchmark_version,
            "schema_version": "1.0",
            "registry_version": registry.registry_version,
            "registry_fingerprint": registry.fingerprint,
            "profile": profile,
            "source_ids": [source.source_id for source in selected],
            "accepted_source_ids": sorted(set(accepted_source_ids) & {source.source_id for source in selected}),
            "source_verification": {
                source_id: report for source_id, report in sorted(source_verification.items())
            },
            "records_sha256": sha256_file(temporary / "gold.jsonl"),
            "stats_sha256": sha256_file(temporary / "stats.json"),
            "image_files": _file_inventory(temporary / "images"),
        }
        dataset_fingerprint = _canonical_hash(fingerprint_basis)
        lock = {
            **fingerprint_basis,
            "dataset_fingerprint": dataset_fingerprint,
            "source_licenses": {
                source.source_id: source.license.spdx for source in selected
            },
            "source_verification": {
                source_id: report for source_id, report in sorted(source_verification.items())
            },
        }
        write_json(temporary / "dataset.lock.json", lock)
        files = _file_inventory(temporary, exclude={"manifest.json"})
        manifest = {
            "schema_version": "1.0",
            "benchmark": registry.benchmark,
            "benchmark_version": benchmark_version,
            "profile": profile,
            "dataset_fingerprint": dataset_fingerprint,
            "registry_fingerprint": registry.fingerprint,
            "page_count": len(records),
            "source_ids": [source.source_id for source in selected],
            "accepted_source_ids": sorted(set(accepted_source_ids) & {source.source_id for source in selected}),
            "source_verification": {
                source_id: report for source_id, report in sorted(source_verification.items())
            },
            "license_tiers": sorted({source.license.tier for source in selected}),
            "stats": stats,
            "audit": {
                "is_valid": audit.is_valid,
                "errors": len(audit.errors),
                "warnings": len(audit.warnings),
            },
            "files": files,
        }
        write_json(temporary / "manifest.json", manifest)

        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
        return CorpusBuildResult(output, len(records), dataset_fingerprint, manifest, audit)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def freeze_corpus(build_root: str | Path) -> dict[str, object]:
    """Verify a build manifest and write an immutable-content marker.

    The marker is intentionally outside the dataset fingerprint basis, avoiding a
    circular hash while binding the build to its manifest and every listed file.
    """

    root = Path(build_root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise BuildError(f"Build has no manifest.json: {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"Cannot read build manifest: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise BuildError("manifest.json must contain an object")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise BuildError("manifest.json has no file inventory")
    verified = 0
    for entry in files:
        if not isinstance(entry, Mapping):
            raise BuildError("manifest file inventory contains a non-object entry")
        relative = str(entry.get("path", ""))
        expected = str(entry.get("sha256", ""))
        path = root / relative
        if not path.is_file():
            raise BuildError(f"Manifest file is missing: {relative}")
        actual = sha256_file(path)
        if actual != expected:
            raise BuildError(f"Manifest hash mismatch for {relative}: expected {expected}, got {actual}")
        verified += 1
    marker = {
        "schema_version": "1.0",
        "benchmark_version": manifest.get("benchmark_version"),
        "dataset_fingerprint": manifest.get("dataset_fingerprint"),
        "manifest_sha256": sha256_file(manifest_path),
        "verified_files": verified,
    }
    write_json(root / "FROZEN.json", marker)
    return marker
