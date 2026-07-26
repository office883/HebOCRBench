"""Official versioned task tracks and their immutable configuration locks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
from pathlib import Path
from typing import Mapping

import yaml

from .config import BenchmarkConfig


class TrackError(ValueError):
    """An official track or its lock is invalid."""


@dataclass(frozen=True, slots=True)
class TrackSpec:
    track_id: str
    version: str
    title: str
    description: str
    official: bool
    task: str
    prediction_mode: str
    accepted_gold_tracks: tuple[str, ...]
    required_prediction_fields: tuple[str, ...]
    primary_metrics: tuple[str, ...]
    benchmark_config: BenchmarkConfig
    config_fingerprint: str
    source_sha256: str
    filename: str

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "official": self.official,
            "task": self.task,
            "prediction_mode": self.prediction_mode,
            "accepted_gold_tracks": list(self.accepted_gold_tracks),
            "required_prediction_fields": list(self.required_prediction_fields),
            "primary_metrics": list(self.primary_metrics),
            "configuration": self.benchmark_config.to_dict(),
            "config_fingerprint": self.config_fingerprint,
            "source_sha256": self.source_sha256,
            "filename": self.filename,
        }


@dataclass(frozen=True, slots=True)
class TrackLockReport:
    valid: bool
    tracks_version: str
    checked: int
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "tracks_version": self.tracks_version,
            "checked": self.checked,
            "issues": list(self.issues),
        }


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_track_bytes(data: bytes, filename: str) -> TrackSpec:
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise TrackError(f"Cannot parse track {filename}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise TrackError(f"Track {filename} must contain a YAML mapping")
    metadata = raw.get("track")
    if not isinstance(metadata, Mapping):
        raise TrackError(f"Track {filename} has no track mapping")
    required_text = ("id", "version", "title", "description", "task", "prediction_mode")
    values: dict[str, str] = {}
    for key in required_text:
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TrackError(f"Track {filename} field track.{key} must be non-empty")
        values[key] = value
    expected_filename = values["id"] + ".yaml"
    if Path(filename).name != expected_filename:
        raise TrackError(f"Track ID {values['id']} does not match filename {Path(filename).name}")
    config_payload = {key: value for key, value in raw.items() if key != "track"}
    config_payload.setdefault("schema_version", str(raw.get("schema_version", "1.0")))
    benchmark_config = BenchmarkConfig.from_mapping(config_payload)

    def text_list(key: str) -> tuple[str, ...]:
        value = metadata.get(key, [])
        if not isinstance(value, list):
            raise TrackError(f"Track {filename} field track.{key} must be a list")
        return tuple(str(item) for item in value)

    return TrackSpec(
        track_id=values["id"],
        version=values["version"],
        title=values["title"],
        description=values["description"],
        official=bool(metadata.get("official", False)),
        task=values["task"],
        prediction_mode=values["prediction_mode"],
        accepted_gold_tracks=text_list("accepted_gold_tracks"),
        required_prediction_fields=text_list("required_prediction_fields"),
        primary_metrics=text_list("primary_metrics"),
        benchmark_config=benchmark_config,
        config_fingerprint=_canonical_hash(raw),
        source_sha256=hashlib.sha256(data).hexdigest(),
        filename=Path(filename).name,
    )


def load_track(name_or_path: str | Path) -> TrackSpec:
    """Load an official packaged track by ID or a track YAML path."""

    candidate = Path(name_or_path)
    if candidate.is_file():
        return _parse_track_bytes(candidate.read_bytes(), candidate.name)
    track_id = str(name_or_path)
    filename = track_id if track_id.endswith(".yaml") else track_id + ".yaml"
    try:
        data = (
            resources.files("hebocrbench")
            .joinpath("data")
            .joinpath("tracks")
            .joinpath(filename)
            .read_bytes()
        )
    except OSError as exc:
        raise TrackError(f"Unknown official track {track_id!r}") from exc
    return _parse_track_bytes(data, filename)


def track_lock_payload(directory: str | Path) -> dict[str, object]:
    root = Path(directory)
    tracks: dict[str, object] = {}
    for path in sorted(root.glob("*.yaml")):
        spec = load_track(path)
        if spec.track_id in tracks:
            raise TrackError(f"Duplicate track ID {spec.track_id}")
        tracks[spec.track_id] = {
            "config_fingerprint": spec.config_fingerprint,
            "filename": spec.filename,
            "prediction_mode": spec.prediction_mode,
            "sha256": spec.source_sha256,
            "task": spec.task,
            "title": spec.title,
        }
    if not tracks:
        raise TrackError(f"No track YAML files found under {root}")
    return {
        "schema_version": "1.0",
        "tracks_version": "1.0.0",
        "tracks": tracks,
    }


def list_official_tracks() -> tuple[TrackSpec, ...]:
    try:
        lock = json.loads(
            resources.files("hebocrbench")
            .joinpath("data")
            .joinpath("tracks")
            .joinpath("tracks.lock.json")
            .read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise TrackError(f"Cannot load packaged track lock: {exc}") from exc
    raw_tracks = lock.get("tracks") if isinstance(lock, Mapping) else None
    if not isinstance(raw_tracks, Mapping):
        raise TrackError("Packaged track lock is malformed")
    return tuple(load_track(str(track_id)) for track_id in sorted(raw_tracks))


def verify_track_lock(directory: str | Path | None = None) -> TrackLockReport:
    """Verify every official YAML byte and canonical configuration fingerprint."""

    issues: list[str] = []
    if directory is None:
        try:
            lock_text = (
                resources.files("hebocrbench")
                .joinpath("data")
                .joinpath("tracks")
                .joinpath("tracks.lock.json")
                .read_text(encoding="utf-8")
            )
            lock = json.loads(lock_text)
        except (OSError, json.JSONDecodeError) as exc:
            return TrackLockReport(False, "unknown", 0, (str(exc),))
        expected = lock.get("tracks", {}) if isinstance(lock, Mapping) else {}
        specs = {spec.track_id: spec for spec in list_official_tracks()}
    else:
        root = Path(directory)
        try:
            lock = json.loads((root / "tracks.lock.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return TrackLockReport(False, "unknown", 0, (str(exc),))
        expected = lock.get("tracks", {}) if isinstance(lock, Mapping) else {}
        specs = {
            load_track(path).track_id: load_track(path) for path in sorted(root.glob("*.yaml"))
        }
    if not isinstance(expected, Mapping):
        return TrackLockReport(False, "unknown", 0, ("tracks lock has no mapping",))
    if set(expected) != set(specs):
        issues.append(
            "track set mismatch: expected="
            + ",".join(sorted(map(str, expected)))
            + " actual="
            + ",".join(sorted(specs))
        )
    for track_id in sorted(set(expected) & set(specs)):
        item = expected[track_id]
        if not isinstance(item, Mapping):
            issues.append(f"{track_id}: lock entry is not a mapping")
            continue
        spec = specs[track_id]
        for key, actual in (
            ("sha256", spec.source_sha256),
            ("config_fingerprint", spec.config_fingerprint),
            ("filename", spec.filename),
            ("task", spec.task),
            ("prediction_mode", spec.prediction_mode),
            ("title", spec.title),
        ):
            if item.get(key) != actual:
                issues.append(f"{track_id}: {key} mismatch")
    version = str(lock.get("tracks_version", "unknown")) if isinstance(lock, Mapping) else "unknown"
    return TrackLockReport(not issues, version, len(specs), tuple(issues))
