"""Command-line interface for HebOCRBench."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .adapters.tesseract import run_tesseract_oracle_layout
from .acquisition import AcquisitionError, fetch_source, verify_source_cache
from .baselines import BASELINE_FACTORIES, generate_baseline_predictions
from .comparison import write_comparison_artifacts
from .certification import certify_release
from .corpus_builder import BuildError, build_corpus, freeze_corpus
from .corpus_registry import RegistryError, load_registry
from .profiles import (
    ProfileError,
    load_profiles,
    profile_fingerprint,
    validate_profile_selection,
)
from .corpus_stats import compute_corpus_stats
from .dataset_audit import audit_dataset
from .config import load_benchmark_config
from .evaluator import evaluate_dataset
from .io import load_jsonl, write_json, write_jsonl
from .modern_score import (
    ModernScoreError,
    combine_modern_track_reports,
    load_modern_report_root,
)
from .modern_suite import (
    ModernSuiteError,
    build_modern_suite_lock,
    load_modern_suite_lock,
    suite_evidence_for_track,
    validate_modern_suite_contract,
)
from .report import write_evaluation_artifacts
from .sanity import run_sanity_matrix
from .stress import DEFAULT_VARIANTS, SUPPORTED_VARIANTS, generate_stress_suite
from .tracks import TrackError, list_official_tracks, load_track, verify_track_lock
from .validator import audit_split_leakage, validate_gold_records, validate_prediction_records


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _comma_values(value: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split(",") if part.strip())
    if not parts:
        raise argparse.ArgumentTypeError("Expected at least one comma-separated value")
    return parts


def _consistent_model_manifest(predictions: Sequence[object]) -> dict[str, object]:
    models: list[dict[str, object]] = []
    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        model = prediction.get("model")
        if isinstance(model, dict):
            normalized = {str(key): value for key, value in model.items()}
            if normalized not in models:
                models.append(normalized)
    if len(models) == 1:
        return models[0]
    if len(models) > 1:
        return {"mixed_models": True, "models": models}
    return {}


def _source_root_values(values: Sequence[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Source root must use SOURCE_ID=PATH syntax: {value!r}")
        source_id, raw_path = value.split("=", 1)
        source_id = source_id.strip()
        if not source_id or not raw_path.strip():
            raise ValueError(f"Invalid source root mapping: {value!r}")
        if source_id in roots:
            raise ValueError(f"Duplicate source root mapping: {source_id}")
        roots[source_id] = Path(raw_path).expanduser()
    return roots


def _text_mapping_values(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Expected KEY=VALUE syntax: {value!r}")
        key, raw = value.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key or not raw:
            raise ValueError(f"Invalid KEY=VALUE mapping: {value!r}")
        if key in result:
            raise ValueError(f"Duplicate mapping key: {key}")
        result[key] = raw
    return result


def _artifact_payload(result) -> dict[str, object]:
    return {
        "source_id": result.source_id,
        "downloaded_count": result.downloaded_count,
        "cache_hit_count": result.cache_hit_count,
        "artifacts": [
            {
                "artifact_id": artifact.artifact_id,
                "path": str(artifact.path),
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
                "from_cache": artifact.from_cache,
                "extracted_to": str(artifact.extracted_to) if artifact.extracted_to else None,
            }
            for artifact in result.artifacts
        ],
    }


def _run_data_command(args: argparse.Namespace) -> int:
    if args.data_command in {"list", "licenses", "profiles", "fetch", "verify", "build", "convert"}:
        registry = load_registry(args.registry)
    if args.data_command == "list":
        sources = registry.select(
            tiers=set(args.tier) if args.tier else None,
            source_ids=set(args.source) if args.source else None,
        )
        _json_print(
            {
                "registry_version": registry.registry_version,
                "registry_fingerprint": registry.fingerprint,
                "sources": [
                    {
                        "source_id": source.source_id,
                        "title": source.title,
                        "version": source.version,
                        "status": source.status,
                        "track": source.track,
                        "languages": list(source.languages),
                        "converter": source.converter,
                        "license": source.license.spdx,
                        "license_tier": source.license.tier,
                        "acceptance_required": source.license.requires_acceptance,
                    }
                    for source in sources
                ],
            }
        )
        return 0
    if args.data_command == "profiles":
        profiles = load_profiles(args.profiles, registry=registry)
        _json_print(
            {
                "profiles_version": profiles.profiles_version,
                "profiles_fingerprint": profiles.fingerprint,
                "registry_fingerprint": registry.fingerprint,
                "profiles": [
                    {
                        "profile_id": profile.profile_id,
                        "title": profile.title,
                        "description": profile.description,
                        "source_ids": list(profile.source_ids),
                        "allowed_license_tiers": list(profile.allowed_license_tiers),
                        "certification_class": profile.certification_class,
                        "score_policy": profile.score_policy,
                    }
                    for profile in profiles.profiles.values()
                ],
            }
        )
        return 0
    if args.data_command == "licenses":
        sources = registry.select(source_ids=set(args.source) if args.source else None)
        _json_print(
            {
                "registry_fingerprint": registry.fingerprint,
                "sources": [
                    {
                        "source_id": source.source_id,
                        "spdx": source.license.spdx,
                        "tier": source.license.tier,
                        "uri": source.license.uri,
                        "authority": source.license.authority,
                        "redistribution": source.license.redistribution,
                        "requires_acceptance": source.license.requires_acceptance,
                        "conflicts": list(source.license.conflicts),
                    }
                    for source in sources
                ],
            }
        )
        return 0
    if args.data_command == "fetch":
        results = []
        for source in registry.select(source_ids=set(args.source)):
            results.append(
                _artifact_payload(
                    fetch_source(
                        source,
                        args.cache,
                        accepted_source_ids=set(args.accept or []),
                        extract=args.extract,
                    )
                )
            )
        _json_print({"sources": results})
        return 0
    if args.data_command == "verify":
        results = [
            _artifact_payload(verify_source_cache(source, args.cache))
            for source in registry.select(source_ids=set(args.source))
        ]
        _json_print({"sources": results, "verified": True})
        return 0
    if args.data_command in {"build", "convert"}:
        selected = set(args.source)
        profiles = None
        if args.profiles is not None or args.registry is None:
            profiles = load_profiles(args.profiles, registry=registry)
        if profiles is not None:
            profile = profiles.profiles.get(args.profile)
            if profile is None and args.profiles is not None:
                raise ProfileError(f"Unknown profile {args.profile!r}")
            if profile is not None:
                selection = validate_profile_selection(
                    profile,
                    selected_source_ids=selected,
                    registry=registry,
                    accepted_source_ids=args.accept or [],
                )
                if not selection.is_valid:
                    details = "; ".join(
                        f"{issue.code}: {issue.message}" for issue in selection.issues
                    )
                    raise ProfileError(details)
        roots = _source_root_values(args.source_root)
        result = build_corpus(
            registry,
            roots,
            args.output,
            source_ids=selected,
            accepted_source_ids=set(args.accept or []),
            benchmark_version=args.benchmark_version,
            profile=args.profile,
            overwrite=args.overwrite,
        )
        _json_print(
            {
                "output": str(result.output_root),
                "page_count": result.page_count,
                "dataset_fingerprint": result.dataset_fingerprint,
                "audit": result.audit.to_dict(),
            }
        )
        return 0
    if args.data_command == "stats":
        stats = compute_corpus_stats(load_jsonl(args.gold))
        if args.output is not None:
            write_json(args.output, stats)
        _json_print(stats)
        return 0
    if args.data_command == "audit":
        report = audit_dataset(load_jsonl(args.gold), args.dataset_root)
        payload = report.to_dict()
        if args.output is not None:
            write_json(args.output, payload)
        _json_print(payload)
        return 0 if report.is_valid else 2
    if args.data_command == "freeze":
        marker = freeze_corpus(args.build_root)
        _json_print(marker)
        return 0
    raise AssertionError(f"Unhandled data command: {args.data_command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hebocrbench",
        description="Unicode-, BiDi-, layout- and robustness-aware Modern Hebrew OCR benchmark",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    tracks = sub.add_parser("tracks", help="Inspect and verify official benchmark tracks")
    tracks_sub = tracks.add_subparsers(dest="tracks_command", required=True)
    tracks_sub.add_parser("list", help="List official locked tracks")
    tracks_show = tracks_sub.add_parser("show", help="Show one official track")
    tracks_show.add_argument("track_id")
    tracks_verify = tracks_sub.add_parser("verify", help="Verify official track lock")
    tracks_verify.add_argument("--directory", type=Path)

    modern_suite = sub.add_parser(
        "modern-suite", help="Build or verify a certified Modern Hebrew suite lock"
    )
    modern_suite_sub = modern_suite.add_subparsers(dest="modern_suite_command", required=True)
    modern_suite_build = modern_suite_sub.add_parser(
        "build", help="Build a suite lock from frozen certified track roots"
    )
    modern_suite_build.add_argument("--registry", type=Path)
    modern_suite_build.add_argument("--profiles", type=Path)
    modern_suite_build.add_argument("--profile", default="modern-hebrew-print-v1")
    modern_suite_build.add_argument("--track-root", required=True, action="append")
    modern_suite_build.add_argument("--maturity", action="append", default=[])
    modern_suite_build.add_argument("--output", required=True, type=Path)
    modern_suite_verify = modern_suite_sub.add_parser(
        "verify", help="Verify a suite lock against canonical release metadata"
    )
    modern_suite_verify.add_argument("--registry", type=Path)
    modern_suite_verify.add_argument("--profiles", type=Path)
    modern_suite_verify.add_argument("--lock", required=True, type=Path)

    generate = sub.add_parser("generate", help="Generate the synthetic diagnostic suite")
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--cases", type=Path)
    generate.add_argument("--font", type=Path)
    generate.add_argument("--seed", type=int, default=20260722)
    generate.add_argument("--limit", type=int)
    generate.add_argument(
        "--variants",
        type=_comma_values,
        default=DEFAULT_VARIANTS,
        help=f"Comma-separated: {', '.join(sorted(SUPPORTED_VARIANTS))}",
    )
    generate.add_argument("--no-structured", action="store_true")

    validate = sub.add_parser("validate", help="Validate gold or prediction JSONL")
    mode = validate.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gold", type=Path)
    mode.add_argument("--predictions", type=Path)
    validate.add_argument("--dataset-root", type=Path)

    audit = sub.add_parser("audit", help="Audit split leakage in gold JSONL")
    audit.add_argument("--gold", required=True, type=Path)

    baseline = sub.add_parser("baseline", help="Generate a deterministic baseline prediction")
    baseline.add_argument("--gold", required=True, type=Path)
    baseline.add_argument("--kind", required=True, choices=sorted(BASELINE_FACTORIES))
    baseline.add_argument("--output", required=True, type=Path)

    tesseract = sub.add_parser(
        "run-tesseract",
        help="Run Tesseract on gold line crops (recognition-only oracle layout)",
    )
    tesseract.add_argument("--gold", required=True, type=Path)
    tesseract.add_argument("--dataset-root", required=True, type=Path)
    tesseract.add_argument("--output", required=True, type=Path)
    tesseract.add_argument("--executable", default="tesseract")
    tesseract.add_argument("--psm", type=int, default=7)
    tesseract.add_argument("--pad", type=int, default=14)

    evaluate = sub.add_parser("evaluate", help="Evaluate a prediction JSONL")
    evaluate.add_argument("--gold", required=True, type=Path)
    evaluate.add_argument("--predictions", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    evaluate.add_argument("--config", type=Path, help="Benchmark YAML configuration")
    evaluate.add_argument("--track", help="Official track ID or track YAML path")
    evaluate.add_argument(
        "--suite-lock",
        type=Path,
        help="Modern Hebrew suite lock binding this official track to frozen gold bytes",
    )
    evaluate.add_argument(
        "--dataset-root",
        type=Path,
        help="Root used to resolve image paths during validation (defaults to the gold file directory)",
    )
    evaluate.add_argument("--skip-validation", action="store_true")

    compare = sub.add_parser("compare", help="Build a certified comparison table from report bundles")
    compare.add_argument("--reports", required=True, type=Path, help="Directory containing report subdirectories")
    compare.add_argument("--output", required=True, type=Path)

    modern_score = sub.add_parser(
        "modern-score",
        help="Combine locked Modern Hebrew track reports into the guarded headline score",
    )
    modern_score.add_argument(
        "--reports",
        required=True,
        type=Path,
        help="Directory containing one canonical report directory per official track",
    )
    modern_score.add_argument("--output", required=True, type=Path)
    modern_score.add_argument(
        "--suite-lock",
        required=True,
        type=Path,
        help="Certified Modern Hebrew suite lock used by every report bundle",
    )

    inspect = sub.add_parser("inspect", help="Print one gold page in logical order")
    inspect.add_argument("--gold", required=True, type=Path)
    inspect.add_argument("--page-id", required=True)

    sanity = sub.add_parser("sanity", help="Run evaluator fault-injection self-tests")
    sanity.add_argument("--output", required=True, type=Path)
    sanity.add_argument("--font", type=Path)
    sanity.add_argument("--seed", type=int, default=20260722)
    sanity.add_argument("--limit", type=int, default=28)
    sanity.add_argument(
        "--variants", type=_comma_values, default=("clean",), help="Comma-separated variants"
    )
    data = sub.add_parser("data", help="Manage licensed real-corpus data")
    data_sub = data.add_subparsers(dest="data_command", required=True)

    data_list = data_sub.add_parser("list", help="List registered corpus sources")
    data_list.add_argument("--registry", type=Path)
    data_list.add_argument("--source", action="append")
    data_list.add_argument("--tier", action="append")

    data_licenses = data_sub.add_parser("licenses", help="Show source license obligations")
    data_licenses.add_argument("--registry", type=Path)
    data_licenses.add_argument("--source", action="append")

    data_profiles = data_sub.add_parser("profiles", help="List official benchmark profiles")
    data_profiles.add_argument("--registry", type=Path)
    data_profiles.add_argument("--profiles", type=Path)

    data_fetch = data_sub.add_parser("fetch", help="Fetch and verify source artifacts")
    data_fetch.add_argument("--registry", type=Path)
    data_fetch.add_argument("--source", required=True, action="append")
    data_fetch.add_argument("--cache", required=True, type=Path)
    data_fetch.add_argument("--accept", action="append")
    data_fetch.add_argument("--extract", action="store_true")

    data_verify = data_sub.add_parser("verify", help="Verify cached source artifacts offline")
    data_verify.add_argument("--registry", type=Path)
    data_verify.add_argument("--source", required=True, action="append")
    data_verify.add_argument("--cache", required=True, type=Path)

    for name, help_text in (("convert", "Convert selected sources into benchmark form"), ("build", "Build an audited corpus")):
        command = data_sub.add_parser(name, help=help_text)
        command.add_argument("--registry", type=Path)
        command.add_argument("--profiles", type=Path)
        command.add_argument("--source", required=True, action="append")
        command.add_argument("--source-root", required=True, action="append")
        command.add_argument("--accept", action="append")
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--profile", default="custom")
        command.add_argument("--benchmark-version", default=__version__)
        command.add_argument("--overwrite", action="store_true")

    data_stats = data_sub.add_parser("stats", help="Compute corpus coverage statistics")
    data_stats.add_argument("--gold", required=True, type=Path)
    data_stats.add_argument("--output", type=Path)

    data_audit = data_sub.add_parser("audit", help="Run integrity and leakage audit")
    data_audit.add_argument("--gold", required=True, type=Path)
    data_audit.add_argument("--dataset-root", required=True, type=Path)
    data_audit.add_argument("--output", type=Path)

    data_freeze = data_sub.add_parser("freeze", help="Verify and freeze a built corpus")
    data_freeze.add_argument("--build-root", required=True, type=Path)

    release = sub.add_parser("release", help="Certify a materialized benchmark release")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    release_certify = release_sub.add_parser("certify", help="Run all HebOCRBench 1.0 certification gates")
    release_certify.add_argument("--build-root", required=True, type=Path)
    release_certify.add_argument("--registry", type=Path)
    release_certify.add_argument("--profiles", type=Path)
    release_certify.add_argument("--expected-version", default=__version__)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "modern-suite":
        try:
            registry = load_registry(args.registry)
            profiles = load_profiles(args.profiles, registry=registry)
            allowed_tracks = {spec.track_id for spec in list_official_tracks()}
            if args.modern_suite_command == "build":
                profile = profiles.profiles.get(args.profile)
                if profile is None:
                    raise ProfileError(f"Unknown profile {args.profile!r}")
                roots = _source_root_values(args.track_root)
                maturity = _text_mapping_values(args.maturity)
                payload = build_modern_suite_lock(
                    roots,
                    profile_id=profile.profile_id,
                    profile_fingerprint=profile_fingerprint(profile),
                    registry_fingerprint=registry.fingerprint,
                    benchmark_version=__version__,
                    suite_version=__version__,
                    maturity=maturity,
                )
                write_json(args.output, payload)
                suite = load_modern_suite_lock(args.output)
            else:
                suite = load_modern_suite_lock(args.lock)
                profile = profiles.profiles.get(suite.profile_id)
                if profile is None:
                    raise ProfileError(f"Unknown suite profile {suite.profile_id!r}")
            validate_modern_suite_contract(
                suite,
                expected_benchmark_version=__version__,
                expected_registry_fingerprint=registry.fingerprint,
                expected_profile_id=profile.profile_id,
                expected_profile_fingerprint=profile_fingerprint(profile),
                allowed_track_ids=allowed_tracks,
            )
            _json_print(
                {
                    "valid": True,
                    "suite_fingerprint": suite.suite_fingerprint,
                    "profile_id": suite.profile_id,
                    "headline_tracks": sorted(
                        track_id for track_id, track in suite.tracks.items() if track.headline
                    ),
                    "output": str(args.output)
                    if args.modern_suite_command == "build"
                    else None,
                }
            )
            return 0
        except (ModernSuiteError, ProfileError, RegistryError, OSError, ValueError) as exc:
            _json_print({"error": str(exc), "error_type": type(exc).__name__})
            return 2

    if args.command == "tracks":
        try:
            if args.tracks_command == "list":
                specs = list_official_tracks()
                _json_print({"tracks": [spec.to_dict() for spec in specs]})
                return 0
            if args.tracks_command == "show":
                _json_print(load_track(args.track_id).to_dict())
                return 0
            report = verify_track_lock(args.directory)
            _json_print(report.to_dict())
            return 0 if report.valid else 2
        except (TrackError, OSError, ValueError) as exc:
            _json_print({"error": str(exc), "error_type": type(exc).__name__})
            return 2
    if args.command == "release":
        registry = load_registry(args.registry)
        profiles = None
        if args.profiles is not None or args.registry is None:
            profiles = load_profiles(args.profiles, registry=registry)
        report = certify_release(
            args.build_root,
            registry,
            expected_version=args.expected_version,
            profiles=profiles,
        )
        _json_print(report.to_dict())
        return 0 if report.certified else 2
    if args.command == "data":
        try:
            return _run_data_command(args)
        except (
            AcquisitionError,
            BuildError,
            ProfileError,
            RegistryError,
            OSError,
            ValueError,
            KeyError,
        ) as exc:
            _json_print({"error": str(exc), "error_type": type(exc).__name__})
            return 2
    if args.command == "generate":
        result = generate_stress_suite(
            args.output,
            cases_path=args.cases,
            seed=args.seed,
            variants=args.variants,
            limit=args.limit,
            font_path=args.font,
            include_structured=not args.no_structured,
        )
        _json_print(result.to_dict())
        return 0

    if args.command == "validate":
        if args.gold is not None:
            report = validate_gold_records(load_jsonl(args.gold), dataset_root=args.dataset_root)
        else:
            report = validate_prediction_records(load_jsonl(args.predictions))
        _json_print(report.to_dict())
        return 0 if report.is_valid else 2

    if args.command == "audit":
        report = audit_split_leakage(load_jsonl(args.gold))
        _json_print(report.to_dict())
        return 0 if report.is_valid else 2

    if args.command == "baseline":
        gold = load_jsonl(args.gold)
        predictions = generate_baseline_predictions(gold, args.kind)
        write_jsonl(args.output, predictions)
        _json_print({"kind": args.kind, "pages": len(predictions), "output": str(args.output)})
        return 0

    if args.command == "run-tesseract":
        gold = load_jsonl(args.gold)
        predictions = run_tesseract_oracle_layout(
            gold,
            dataset_root=args.dataset_root,
            executable=args.executable,
            psm=args.psm,
            pad=args.pad,
        )
        write_jsonl(args.output, predictions)
        _json_print(
            {
                "adapter": "tesseract_oracle_layout",
                "pages": len(predictions),
                "output": str(args.output),
                "warning": "Oracle layout: recognition score only; do not compare as end-to-end layout OCR.",
            }
        )
        return 0

    if args.command == "evaluate":
        gold = load_jsonl(args.gold)
        predictions = load_jsonl(args.predictions)
        if not args.skip_validation:
            dataset_root = args.dataset_root if args.dataset_root is not None else args.gold.parent
            gold_report = validate_gold_records(gold, dataset_root=dataset_root)
            prediction_report = validate_prediction_records(predictions)
            if not gold_report.is_valid or not prediction_report.is_valid:
                _json_print(
                    {
                        "error": "Input validation failed",
                        "gold": gold_report.to_dict(),
                        "predictions": prediction_report.to_dict(),
                    }
                )
                return 2
        track_spec = load_track(args.track) if args.track is not None else None
        if track_spec is not None and args.config is not None:
            _json_print({"error": "Use either --track or --config, not both"})
            return 2
        config = (
            track_spec.benchmark_config
            if track_spec is not None
            else load_benchmark_config(args.config) if args.config is not None else None
        )
        if track_spec is not None:
            unexpected = sorted(
                {str(page.get("track", "")) for page in gold}
                - set(track_spec.accepted_gold_tracks)
            )
            if unexpected:
                _json_print(
                    {
                        "error": "Gold pages are outside the selected track contract",
                        "track_id": track_spec.track_id,
                        "unexpected_gold_tracks": unexpected,
                    }
                )
                return 2
        suite_evidence = None
        if args.suite_lock is not None:
            if track_spec is None:
                _json_print({"error": "--suite-lock requires --track"})
                return 2
            try:
                suite_evidence = suite_evidence_for_track(
                    load_modern_suite_lock(args.suite_lock),
                    track_spec.track_id,
                    args.gold,
                )
            except ModernSuiteError as exc:
                _json_print({"error": str(exc)})
                return 2

        run = evaluate_dataset(gold, predictions, config=config)
        if track_spec is not None:
            run.configuration.update(
                {
                    "official_track_id": track_spec.track_id,
                    "official_track_version": track_spec.version,
                    "official_track_fingerprint": track_spec.config_fingerprint,
                }
            )
        paths = write_evaluation_artifacts(
            run,
            args.output,
            gold_path=args.gold,
            predictions_path=args.predictions,
            model_manifest=_consistent_model_manifest(predictions),
            suite_evidence=suite_evidence,
        )
        _json_print(
            {
                "conformance": run.metrics["conformance"]["status"],
                "line_gcer": run.metrics["recognition"]["line_gcer"],
                "page_order_gcer": run.metrics["recognition"]["page_order_gcer"],
                "track_id": track_spec.track_id if track_spec is not None else None,
                "track_fingerprint": (
                    track_spec.config_fingerprint if track_spec is not None else None
                ),
                "artifacts": {key: str(path) for key, path in paths.items()},
            }
        )
        return 0

    if args.command == "compare":
        paths = write_comparison_artifacts(args.reports, args.output)
        payload = json.loads(paths["json"].read_text(encoding="utf-8"))
        _json_print(
            {
                "runs": len(payload["runs"]),
                "certified_runs": sum(
                    row.get("certified_rank") is not None for row in payload["runs"]
                ),
                "artifacts": {key: str(path) for key, path in paths.items()},
            }
        )
        return 0

    if args.command == "modern-score":
        try:
            suite = load_modern_suite_lock(args.suite_lock)
            result = combine_modern_track_reports(
                load_modern_report_root(args.reports),
                suite_lock=suite,
            )
        except (ModernScoreError, ModernSuiteError) as exc:
            _json_print({"error": str(exc)})
            return 2
        write_json(args.output, result)
        _json_print(
            {
                "status": result["status"],
                "headline_score": result["headline_score"],
                "output": str(args.output),
            }
        )
        return 0 if result["status"] == "rankable" else 2

    if args.command == "inspect":
        pages = {str(page["page_id"]): page for page in load_jsonl(args.gold)}
        if args.page_id not in pages:
            print(f"Unknown page_id: {args.page_id}", file=sys.stderr)
            return 2
        _json_print(pages[args.page_id])
        return 0

    if args.command == "sanity":
        result = run_sanity_matrix(
            args.output,
            variants=args.variants,
            limit=args.limit,
            seed=args.seed,
            font_path=args.font,
        )
        _json_print(result)
        return 0 if result["passed"] else 3

    raise AssertionError(f"Unhandled command: {args.command}")
