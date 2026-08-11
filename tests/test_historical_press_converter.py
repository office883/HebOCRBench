from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from hebocrbench.converters import ConversionContext
from hebocrbench.converters.historical_press import (
    HistoricalPressConversionError,
    convert_historical_press_pagealto_file,
    validate_historical_press_corpus,
)


PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15">
  <Page imageFilename="page.png" imageWidth="100" imageHeight="80">
    <TextRegion id="r1">
      <Coords points="5,5 95,5 95,70 5,70"/>
      <TextLine id="l1">
        <Coords points="10,20 90,20 90,40 10,40"/>
        <Baseline points="10,35 90,35"/>
        <TextEquiv><Unicode>֦שלום עולם</Unicode></TextEquiv>
      </TextLine>
    </TextRegion>
  </Page>
</PcGts>
"""

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout><Page WIDTH="100" HEIGHT="80"><PrintSpace>
    <TextBlock ID="r1"><TextLine ID="l1"><String CONTENT="֦שלוםעולם"/></TextLine></TextBlock>
  </PrintSpace></Page></Layout>
</alto>
"""


def _context() -> ConversionContext:
    return ConversionContext(
        source_id="historical-hebrew-press-mixed-v1",
        source_version="locked",
        split="test",
        track="historical_hebrew_press_mixed",
        license_expression="LicenseRef-Fixture",
        rights_uri="https://example.invalid/rights",
        redistribution="federated-only",
        citation_key="fixture",
        source_url="https://example.invalid/source",
        metadata_defaults={"pure_rashi_claim": False},
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    collection = tmp_path / "collection"
    page_dir = collection / "page"
    alto_dir = collection / "alto"
    page_dir.mkdir(parents=True)
    alto_dir.mkdir(parents=True)
    (page_dir / "page.xml").write_text(PAGE, encoding="utf-8")
    (alto_dir / "page.xml").write_text(ALTO, encoding="utf-8")
    Image.new("RGB", (100, 80), "white").save(collection / "page.png")
    return page_dir / "page.xml", collection


def test_historical_press_converter_uses_page_gold_and_verifies_alto(tmp_path: Path):
    annotation, image_root = _fixture(tmp_path)

    record = convert_historical_press_pagealto_file(
        annotation,
        tmp_path,
        image_root,
        _context(),
    )

    line = record["regions"][0]["lines"][0]
    metadata = record["metadata"]
    assert line["text"] == "֦שלום עולם"
    assert line["tags"] == ["source-dangling-combining-mark-preserved"]
    assert line["uncertain_spans"][0]["codepoint"] == "U+05A6"
    assert metadata["page_alto_line_identity_parity"] is True
    assert metadata["page_alto_nonwhitespace_text_parity"] is True
    assert metadata["rashi_region_or_line_labels_available"] is False
    assert metadata["pure_rashi_claim"] is False
    validate_historical_press_corpus([record], expected_pages=1, expected_lines=1)


def test_historical_press_converter_fails_closed_on_alto_line_identity(tmp_path: Path):
    annotation, image_root = _fixture(tmp_path)
    paired_alto = tmp_path / "collection" / "alto" / "page.xml"
    paired_alto.write_text(ALTO.replace('ID="l1"', 'ID="different"'), encoding="utf-8")

    with pytest.raises(HistoricalPressConversionError, match="line identities disagree"):
        convert_historical_press_pagealto_file(
            annotation,
            tmp_path,
            image_root,
            _context(),
        )


def test_historical_press_inventory_is_exact(tmp_path: Path):
    annotation, image_root = _fixture(tmp_path)
    record = convert_historical_press_pagealto_file(
        annotation,
        tmp_path,
        image_root,
        _context(),
    )

    with pytest.raises(HistoricalPressConversionError, match="inventory mismatch"):
        validate_historical_press_corpus([record], expected_pages=34, expected_lines=4016)
