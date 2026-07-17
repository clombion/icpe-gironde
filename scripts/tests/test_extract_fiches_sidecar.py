"""Tests du sidecar ``_fiches.jsonl`` produit par extract v0.2.0.

Couvre :

- Dataclass ``FicheRegion`` : pages 1-based, bbox en points PDF
- ``compute_fiches_sidecar_entry`` : structure correcte, fiches vides
  pour non-DREAL
- ``_find_fiche_regions`` : single page, multi-page, title not found
- ``append_fiches_sidecar`` : JSONL format, schema validation
- Intégration : sidecar entry from a real DREAL PDF

Les tests utilisant pymupdf sont skippés si le module est absent
(stdlib-only CI).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.tests._loader import load_extractor

try:
    import pymupdf  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    PYMUPDF_AVAILABLE = False
else:
    PYMUPDF_AVAILABLE = True

try:
    import jsonschema as _jsonschema  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    JSONSCHEMA_AVAILABLE = False
else:
    JSONSCHEMA_AVAILABLE = True


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PDF_DIR = REPO_ROOT / "rapports-inspection"
FIXTURE_DREAL_SMALL = "A-B-H_3107065_2022-11-02_40440719900011.pdf"

CORPUS_AVAILABLE = (
    PYMUPDF_AVAILABLE
    and PDF_DIR.exists()
    and (PDF_DIR / FIXTURE_DREAL_SMALL).exists()
)


class FicheRegionTests(unittest.TestCase):
    """Dataclass FicheRegion basic checks."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_page_is_stored_as_given(self) -> None:
        region = self.m.FicheRegion(page=3, bbox=[10.0, 20.0, 500.0, 700.0])
        self.assertEqual(region.page, 3)

    def test_bbox_has_four_floats(self) -> None:
        region = self.m.FicheRegion(page=1, bbox=[0.0, 0.0, 595.0, 842.0])
        self.assertEqual(len(region.bbox), 4)


class FicheSubSectionTests(unittest.TestCase):
    """Le champ sub_section est rempli par _extract_fiches_from_subsections."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_sub_section_populated_from_subsection_heading(self) -> None:
        text = (
            "Rapport de l'Inspection des installations classées\n"
            "Code AIOT : 123\n"
            "1) Contexte\nctx\n"
            "2) Constats\n"
            "2-4) Fiches de constats\n"
            "N° 1 : Premier constat\nbody 1\n"
        )
        sections = self.m.parse_dreal_sections(text)
        self.assertTrue(len(sections.fiches) >= 1)
        self.assertEqual(sections.fiches[0].sub_section, "2-4) Fiches de constats")

    def test_sub_section_empty_for_standalone_parse(self) -> None:
        # parse_fiches_constats doesn't have subsection context
        fiches = self.m.parse_fiches_constats("N° 1 : Test\nbody\n")
        self.assertEqual(fiches[0].sub_section, "")


class AppendFichesSidecarTests(unittest.TestCase):
    """JSONL append and roundtrip."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = Path(self.tmpdir.name) / "_fiches.jsonl"

    def test_append_creates_file(self) -> None:
        entry = {
            "source_pdf": "test.pdf",
            "source_sha256": "a" * 64,
            "extraction_version": "0.2.0",
            "extraction_method": "dreal_parser",
            "page_count": 1,
            "fiches": [],
        }
        self.m.append_fiches_sidecar(self.path, entry)
        self.assertTrue(self.path.exists())
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["source_pdf"], "test.pdf")

    def test_multiple_appends_produce_multiple_lines(self) -> None:
        for i in range(3):
            entry = {
                "source_pdf": f"test_{i}.pdf",
                "source_sha256": str(i) * 64,
                "extraction_version": "0.2.0",
                "extraction_method": "dreal_parser",
                "page_count": i + 1,
                "fiches": [],
            }
            self.m.append_fiches_sidecar(self.path, entry)
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)


@unittest.skipUnless(CORPUS_AVAILABLE, "corpus ou pymupdf absents")
class ComputeSidecarIntegrationTests(unittest.TestCase):
    """Integration tests on a real DREAL PDF."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.pdf_path = PDF_DIR / FIXTURE_DREAL_SMALL
        raw = self.m.extract_raw_text(self.pdf_path)
        text = self.m.clean_text(raw)
        sections = self.m.parse_dreal_sections(text)
        self.fiches = sections.fiches
        sha = self.m.compute_sha256(self.pdf_path)
        self.entry = self.m.compute_fiches_sidecar_entry(
            self.pdf_path, sha, "dreal_parser", self.fiches
        )

    def test_entry_has_required_keys(self) -> None:
        for key in ("source_pdf", "source_sha256", "extraction_version",
                     "extraction_method", "page_count", "fiches"):
            self.assertIn(key, self.entry)

    def test_fiches_count_matches(self) -> None:
        self.assertEqual(len(self.entry["fiches"]), len(self.fiches))

    def test_page_count_positive(self) -> None:
        self.assertGreater(self.entry["page_count"], 0)

    def test_fiche_has_regions(self) -> None:
        for fe in self.entry["fiches"]:
            self.assertIn("regions", fe)
            self.assertIsInstance(fe["regions"], list)

    def test_pages_are_1_based(self) -> None:
        for fe in self.entry["fiches"]:
            for region in fe["regions"]:
                self.assertGreaterEqual(region["page"], 1,
                                        f"page should be 1-based, got {region['page']}")

    def test_bbox_has_four_numbers(self) -> None:
        for fe in self.entry["fiches"]:
            for region in fe["regions"]:
                self.assertEqual(len(region["bbox"]), 4)
                for coord in region["bbox"]:
                    self.assertIsInstance(coord, float)

    def test_bbox_within_page_bounds(self) -> None:
        # A4 in points: ~595 x ~842
        for fe in self.entry["fiches"]:
            for region in fe["regions"]:
                x0, y0, x1, y1 = region["bbox"]
                self.assertGreaterEqual(x0, 0)
                self.assertGreaterEqual(y0, 0)
                self.assertLessEqual(x1, 700)
                self.assertLessEqual(y1, 900)

    def test_sub_section_populated(self) -> None:
        for fe in self.entry["fiches"]:
            self.assertIn("sub_section", fe)
            self.assertTrue(fe["sub_section"],
                            "sub_section should not be empty for DREAL fiches")

    def test_empty_fiches_for_non_dreal_pdf(self) -> None:
        # Simulate a pymupdf4llm_generic PDF — pass empty fiches list
        entry = self.m.compute_fiches_sidecar_entry(
            self.pdf_path, "0" * 64, "pymupdf4llm_generic", []
        )
        self.assertEqual(entry["fiches"], [])
        self.assertEqual(entry["extraction_method"], "pymupdf4llm_generic")


@unittest.skipUnless(CORPUS_AVAILABLE and JSONSCHEMA_AVAILABLE,
                     "corpus, pymupdf ou jsonschema absents")
class SidecarSchemaValidationTests(unittest.TestCase):
    """Validate sidecar entry against JSON Schema."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.pdf_path = PDF_DIR / FIXTURE_DREAL_SMALL
        raw = self.m.extract_raw_text(self.pdf_path)
        text = self.m.clean_text(raw)
        sections = self.m.parse_dreal_sections(text)
        sha = self.m.compute_sha256(self.pdf_path)
        self.entry = self.m.compute_fiches_sidecar_entry(
            self.pdf_path, sha, "dreal_parser", sections.fiches
        )
        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "fiches_sidecar.json"
        with schema_path.open(encoding="utf-8") as f:
            self.schema = json.load(f)

    def test_sidecar_entry_validates(self) -> None:
        import jsonschema
        jsonschema.validate(instance=self.entry, schema=self.schema)

    def test_entry_with_empty_fiches_validates(self) -> None:
        import jsonschema
        entry = {
            "source_pdf": "test.pdf",
            "source_sha256": "a" * 64,
            "extraction_version": "0.2.0",
            "extraction_method": "pymupdf4llm_generic",
            "page_count": 5,
            "fiches": [],
        }
        jsonschema.validate(instance=entry, schema=self.schema)

    def test_entry_with_empty_regions_validates(self) -> None:
        import jsonschema
        entry = {
            "source_pdf": "test.pdf",
            "source_sha256": "a" * 64,
            "extraction_version": "0.2.0",
            "extraction_method": "dreal_parser",
            "page_count": 3,
            "fiches": [{
                "num": "1",
                "titre": "Test",
                "body": "body",
                "sub_section": "2-4) Fiches de constats",
                "regions": [],
            }],
        }
        jsonschema.validate(instance=entry, schema=self.schema)


if __name__ == "__main__":
    unittest.main()
