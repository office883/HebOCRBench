from __future__ import annotations

from pathlib import Path

from PIL import Image

from hebocrbench.converters import ConversionContext
from hebocrbench.converters.pagexml import convert_pagexml_file
from hebocrbench.validator import validate_gold_records


def _context() -> ConversionContext:
    return ConversionContext(
        source_id="pinkas-fixture",
        source_version="1",
        split="test",
        track="modern_page_ocr",
        license_expression="CC-BY-4.0",
        rights_uri="https://creativecommons.org/licenses/by/4.0/",
        redistribution="allowed",
        citation_key="pinkas-fixture",
        source_url="https://example.invalid/pinkas",
        metadata_defaults={
            "languages": ["he", "en"],
            "script": "Hebr",
            "script_style": "modern_square_print",
            "era": "modern",
            "document_type": "manuscript",
            "layout_type": "two_column",
            "vocalization": "none",
            "source_type": "scan",
            "source_collection": "fixture",
        },
    )


def test_pagexml_converter_preserves_unicode_baselines_and_explicit_reading_order(tmp_path):
    image_root = tmp_path / "images"
    image_root.mkdir()
    Image.new("RGB", (1200, 800), "white").save(image_root / "page.jpg")

    record = convert_pagexml_file(Path("tests/fixtures/page/sample.xml"), image_root, _context())

    assert record["page_id"] == "pinkas-fixture-page"
    assert record["document_id"] == "pinkas-fixture"
    assert record["image"]["path"] == "page.jpg"
    assert record["image"]["width"] == 1200
    assert record["reading_order"]["edges"] == [["r-right", "r-left"]]
    regions = {region["region_id"]: region for region in record["regions"]}
    assert regions["r-right"]["base_direction"] == "rtl"
    assert regions["r-left"]["base_direction"] == "ltr"
    assert regions["r-right"]["lines"][0]["text"] == "בשנת 2026"
    assert regions["r-right"]["lines"][1]["text"] == "שלום עולם"
    assert regions["r-right"]["lines"][0]["baseline"] == [[1100.0, 205.0], [680.0, 205.0]]
    assert record["metadata"]["source_id"] == "pinkas-fixture"
    assert record["metadata"]["source_annotation_path"].endswith("sample.xml")
    assert record["metadata"]["citation_key"] == "pinkas-fixture"
    assert validate_gold_records([record], dataset_root=image_root).is_valid


def test_pagexml_converter_does_not_reverse_or_x_sort_mixed_text(tmp_path):
    Image.new("RGB", (1200, 800), "white").save(tmp_path / "page.jpg")

    record = convert_pagexml_file(Path("tests/fixtures/page/sample.xml"), tmp_path, _context())
    text = record["regions"][1]["lines"][0]["text"]

    assert text == "בשנת 2026"
    assert text != text[::-1]


def test_pagexml_converter_repairs_self_touching_contours_and_audits_change(tmp_path):
    Image.new("RGB", (100, 100), "white").save(tmp_path / "repair.jpg")
    annotation = tmp_path / "repair.xml"
    annotation.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2018-07-15">
  <Page imageFilename="repair.jpg" imageWidth="100" imageHeight="100">
    <TextRegion id="r1" type="paragraph">
      <Coords points="10,10 90,90 10,90 90,10"/>
      <TextLine id="l1">
        <Coords points="10,20 90,20 90,40 10,40"/>
        <TextEquiv><Unicode>שלום</Unicode></TextEquiv>
      </TextLine>
    </TextRegion>
  </Page>
</PcGts>""",
        encoding="utf-8",
    )

    record = convert_pagexml_file(annotation, tmp_path, _context())

    repairs = record["metadata"]["geometry_repairs"]
    assert record["metadata"]["geometry_repair_count"] == 1
    assert repairs[0]["element_id"] == "r1"
    assert repairs[0]["element_type"] == "page_text_region"
    assert repairs[0]["method"] == "buffer_0"
    assert validate_gold_records([record], dataset_root=tmp_path).is_valid
