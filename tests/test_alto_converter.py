from __future__ import annotations

from pathlib import Path

from PIL import Image

from hebocrbench.converters import ConversionContext
from hebocrbench.converters.alto import convert_alto_file
from hebocrbench.validator import validate_gold_records


def _context() -> ConversionContext:
    return ConversionContext(
        source_id="biblia-fixture",
        source_version="1.0",
        split="dev",
        track="modern_page_ocr",
        license_expression="CC-BY-NC-SA-4.0",
        rights_uri="https://creativecommons.org/licenses/by-nc-sa/4.0/",
        redistribution="conditional",
        citation_key="biblia-fixture",
        source_url="https://example.invalid/biblia",
        metadata_defaults={
            "languages": ["he", "en"],
            "script": "Hebr",
            "script_style": "medieval_bookhand",
            "era": "medieval",
            "document_type": "manuscript",
            "layout_type": "two_column",
            "vocalization": "mixed",
            "source_type": "scan",
            "source_collection": "fixture",
        },
    )


def test_alto_converter_preserves_xml_logical_order_and_mixed_bidi(tmp_path):
    Image.new("RGB", (1000, 600), "white").save(tmp_path / "alto-page.png")

    record = convert_alto_file(Path("tests/fixtures/alto/sample.xml"), tmp_path, _context())

    regions = {region["region_id"]: region for region in record["regions"]}
    assert regions["b1"]["lines"][0]["text"] == "בשנת 2026 OCR-v2.1"
    assert regions["b1"]["lines"][0]["text"] != "OCR-v2.1 2026 בשנת"
    assert regions["b1"]["lines"][1]["text"] == "שלום ־ עולם"
    assert regions["b1"]["base_direction"] == "rtl"
    assert regions["b2"]["base_direction"] == "ltr"
    assert regions["b2"]["polygon"] == [[50.0, 50.0], [400.0, 50.0], [400.0, 500.0], [50.0, 500.0]]
    assert record["reading_order"]["edges"] == [["b1", "b2"]]
    assert record["metadata"]["source_id"] == "biblia-fixture"
    assert validate_gold_records([record], dataset_root=tmp_path).is_valid


def test_alto_converter_uses_glyphs_when_string_content_is_missing(tmp_path):
    Image.new("RGB", (1000, 600), "white").save(tmp_path / "alto-page.png")

    record = convert_alto_file(Path("tests/fixtures/alto/sample.xml"), tmp_path, _context())

    assert "שלום" in record["regions"][0]["lines"][1]["text"]


def test_alto_converter_accepts_flat_points_and_flat_baseline(tmp_path):
    image_path = tmp_path / "flat.jpg"
    Image.new("RGB", (200, 120), "white").save(image_path)
    annotation = tmp_path / "flat.xml"
    annotation.write_text(
        '''<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description><sourceImageInformation><fileName>flat.jpg</fileName></sourceImageInformation></Description>
  <Layout><Page WIDTH="200" HEIGHT="120"><PrintSpace>
    <TextBlock ID="b1" HPOS="10" VPOS="10" WIDTH="180" HEIGHT="90">
      <Shape><Polygon POINTS="10 10 190 10 190 100 10 100"/></Shape>
      <TextLine ID="l1" HPOS="20" VPOS="20" WIDTH="160" HEIGHT="30"
                BASELINE="20 45 100 43 180 45">
        <Shape><Polygon POINTS="20 20 180 20 180 50 20 50"/></Shape>
        <String CONTENT="שלום"/>
      </TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>''',
        encoding="utf-8",
    )

    record = convert_alto_file(annotation, tmp_path, _context())

    line = record["regions"][0]["lines"][0]
    assert line["polygon"] == [[20.0, 20.0], [180.0, 20.0], [180.0, 50.0], [20.0, 50.0]]
    assert line["baseline"] == [[20.0, 45.0], [100.0, 43.0], [180.0, 45.0]]
    assert validate_gold_records([record], dataset_root=tmp_path).is_valid


def test_alto_converter_records_rectangle_fallback_for_degenerate_source_polygon(tmp_path):
    Image.new("RGB", (200, 120), "white").save(tmp_path / "fallback.jpg")
    annotation = tmp_path / "fallback.xml"
    annotation.write_text(
        '''<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description><sourceImageInformation><fileName>fallback.jpg</fileName></sourceImageInformation></Description>
  <Layout><Page WIDTH="200" HEIGHT="120"><PrintSpace>
    <TextBlock ID="b1" HPOS="10" VPOS="10" WIDTH="180" HEIGHT="90">
      <Shape><Polygon POINTS="10 10 10 10 10 10"/></Shape>
      <TextLine ID="l1" HPOS="20" VPOS="20" WIDTH="160" HEIGHT="30">
        <String CONTENT="שלום"/>
      </TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>''',
        encoding="utf-8",
    )

    record = convert_alto_file(annotation, tmp_path, _context())

    assert record["regions"][0]["polygon"] == [
        [10.0, 10.0],
        [190.0, 10.0],
        [190.0, 100.0],
        [10.0, 100.0],
    ]
    repairs = record["metadata"]["geometry_repairs"]
    assert repairs == [
        {
            "element_id": "b1",
            "element_type": "alto_text_block",
            "method": "rectangle_fallback",
            "reason": "source polygon was unusable",
            "original_point_count": 0,
            "repaired_point_count": 4,
            "repaired_area": 16200.0,
        }
    ]
    assert validate_gold_records([record], dataset_root=tmp_path).is_valid
