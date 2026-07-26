"""Converters from external OCR ground-truth formats to HebOCRBench records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ConversionContext:
    """Immutable provenance and benchmark defaults applied during conversion."""

    source_id: str
    source_version: str
    split: str
    track: str
    license_expression: str
    rights_uri: str
    redistribution: str
    citation_key: str
    source_url: str
    metadata_defaults: Mapping[str, object] = field(default_factory=dict)

    def metadata(self, *, annotation_path: str) -> dict[str, object]:
        result = dict(self.metadata_defaults)
        result.update(
            {
                "source_id": self.source_id,
                "source_version": self.source_version,
                "source_annotation_path": annotation_path,
                "source_url": self.source_url,
                "citation_key": self.citation_key,
                "license": self.license_expression,
                "rights_uri": self.rights_uri,
                "redistribution": self.redistribution,
            }
        )
        return result


__all__ = ["ConversionContext"]
