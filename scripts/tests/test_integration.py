"""Tests d'intégration du pipeline sur de vrais PDFs du corpus.

Ces tests s'exécutent contre ``rapports-inspection/`` et valident
que le pipeline complet (classify → parse → render → validate)
produit les résultats attendus pour plusieurs gabarits réels.

Si le corpus n'est pas disponible (run hors repo, CI sans data),
chaque test est marqué ``skipUnless`` — pas d'erreur bruyante.

Dépendances : pymupdf (pour l'extraction texte), jsonschema (pour
la validation du front matter). Lancement :

    uv run -m unittest scripts.tests.test_integration
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path

from scripts.tests._loader import load_extractor

try:
    import pymupdf  # noqa: F401
    import pymupdf4llm  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    PYMUPDF_AVAILABLE = False
else:
    PYMUPDF_AVAILABLE = True


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PDF_DIR = REPO_ROOT / "rapports-inspection"

# Fixtures de corpus — les PDFs sont dans ``rapports-inspection/``,
# commités avec le projet. On en choisit deux petits et un moyen
# pour couvrir les variations de taille et de structure des
# rapports DREAL standard.
FIXTURE_DREAL_SMALL = (
    "A-B-H_3107065_2022-11-02_40440719900011.pdf"
)
FIXTURE_DREAL_MEDIUM = (
    "5A-IMMOBILIERE-SCI_100007752_2022-10-28_77814766000030.pdf"
)

CORPUS_AVAILABLE = (
    PYMUPDF_AVAILABLE
    and PDF_DIR.exists()
    and (PDF_DIR / FIXTURE_DREAL_SMALL).exists()
)


def _csv_row_for(pdf_filename: str, id_icpe: str, siret: str) -> dict[str, str]:
    """Simule une ligne de rapports-inspection.csv pour les tests."""
    return {
        "id_icpe": id_icpe,
        "nom_complet": pdf_filename.split("_")[0].replace("-", " "),
        "siret": siret,
        "date_inspection": "2022-01-01",
        "identifiant_fichier": "fakeIdentifier123",
        "nom_fichier_local": pdf_filename,
        "url_source_georisques": (
            "https://www.georisques.gouv.fr/webappReport/ws/"
            "installations/inspection/fakeIdentifier123"
        ),
        "url_pages": (
            "https://bononlouis-del.github.io/"
            "Les-ICPE-en-r-serve-naturelle-nationale/"
            f"rapports-inspection/{pdf_filename}"
        ),
        "statut_telechargement": "skip",
    }


@unittest.skipUnless(CORPUS_AVAILABLE, "corpus ou pymupdf absents")
class ExtractDrealSmallTests(unittest.TestCase):
    """Extraction complète d'un PDF DREAL court."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.pdf_path = PDF_DIR / FIXTURE_DREAL_SMALL
        self.csv_row = _csv_row_for(FIXTURE_DREAL_SMALL, "3107065", "40440719900011")
        self.now = dt.datetime(2026, 4, 8, 12, 0, 0)
        self.result = self.m.extract_pdf(
            self.pdf_path, self.csv_row, allow_ocr=False, now=self.now
        )

    def test_method_is_dreal_parser(self) -> None:
        self.assertEqual(
            self.result.method,
            self.m.ExtractionMethod.DREAL_PARSER,
        )

    def test_no_error(self) -> None:
        self.assertIsNone(self.result.error)

    def test_front_matter_returned(self) -> None:
        self.assertIsNotNone(self.result.front_matter)

    def test_markdown_starts_with_front_matter(self) -> None:
        self.assertTrue(self.result.markdown.startswith("---\n"))

    def test_markdown_contains_dreal_structure(self) -> None:
        md = self.result.markdown
        self.assertIn("# Rapport d'inspection —", md)
        self.assertIn("## 1) Contexte", md)
        self.assertIn("## 2) Constats", md)
        # A-B-H a bien les 4 sous-sections DREAL standard.
        self.assertIn("### 2-1) Introduction", md)
        self.assertIn("### 2-4) Fiches de constats", md)

    def test_metadata_extracted_correctly(self) -> None:
        fm = self.result.front_matter
        assert fm is not None
        self.assertEqual(fm["source_pdf"], FIXTURE_DREAL_SMALL)
        self.assertEqual(fm["id_icpe"], "3107065")
        self.assertEqual(fm["siret"], "40440719900011")
        self.assertEqual(fm["extraction_method"], "dreal_parser")
        self.assertEqual(fm["extraction_version"], self.m.EXTRACTION_VERSION)
        self.assertEqual(fm["extracted_at"], "2026-04-08T12:00:00")

    def test_front_matter_passes_schema_validation(self) -> None:
        schema = self.m.load_schema()
        fm = self.result.front_matter
        assert fm is not None
        # Lève si invalide.
        self.m.validate_front_matter_against_schema(fm, schema)

    def test_markdown_sha256_stable_within_run(self) -> None:
        # Rejouer extract_pdf avec la même horloge produit EXACTEMENT
        # le même markdown : déterminisme complet en l'absence de
        # source changée.
        replay = self.m.extract_pdf(
            self.pdf_path, self.csv_row, allow_ocr=False, now=self.now
        )
        self.assertEqual(self.result.markdown, replay.markdown)


@unittest.skipUnless(CORPUS_AVAILABLE, "corpus ou pymupdf absents")
class ExtractDrealMediumTests(unittest.TestCase):
    """Extraction complète d'un PDF DREAL plus gros (26k chars).

    Vérifie que le parser DREAL gère un volume réaliste sans crasher
    ni perdre de contenu (test rapide de non-régression sur fiches
    nombreuses).
    """

    def setUp(self) -> None:
        self.m = load_extractor()
        self.pdf_path = PDF_DIR / FIXTURE_DREAL_MEDIUM
        csv_row = _csv_row_for(
            FIXTURE_DREAL_MEDIUM, "100007752", "77814766000030"
        )
        self.result = self.m.extract_pdf(
            self.pdf_path,
            csv_row,
            allow_ocr=False,
            now=dt.datetime(2026, 4, 8, 12, 0, 0),
        )

    def test_extraction_succeeds(self) -> None:
        self.assertEqual(
            self.result.method,
            self.m.ExtractionMethod.DREAL_PARSER,
        )

    def test_markdown_nonempty_and_sizeable(self) -> None:
        # Un PDF de 26k chars doit produire un markdown d'au moins
        # 10k chars (front matter + sections + fiches rendues).
        self.assertGreater(len(self.result.markdown), 10_000)

    def test_multiple_fiches_rendered(self) -> None:
        # 5A-IMMOBILIERE a 11 fiches dans son rapport d'inspection.
        # On attend au moins 3 fiches distinctes rendues en H4.
        fiche_count = self.result.markdown.count("#### Fiche N°")
        self.assertGreaterEqual(
            fiche_count,
            3,
            f"attendu ≥3 fiches H4, vu {fiche_count}",
        )


@unittest.skipUnless(CORPUS_AVAILABLE, "corpus ou pymupdf absents")
class NoOcrModeTests(unittest.TestCase):
    """Le flag ``allow_ocr=False`` marque les scans comme FAILED sans OCR."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_scan_fails_without_ocr(self) -> None:
        scan_name = "ABATTOIR-INTERCOMMUNAL-DU-BAZADAIS_5200368_2025-12-22_82984165900016.pdf"
        pdf_path = PDF_DIR / scan_name
        if not pdf_path.exists():
            self.skipTest(f"scan fixture {scan_name} absent")
        # Skippe aussi si le PDF a déjà été OCR'é par un run précédent.
        if self.m.has_text_layer(pdf_path):
            self.skipTest(
                f"{scan_name} déjà OCR'é par un run précédent"
            )
        csv_row = _csv_row_for(scan_name, "5200368", "82984165900016")
        result = self.m.extract_pdf(
            pdf_path, csv_row, allow_ocr=False,
            now=dt.datetime(2026, 4, 8, 12, 0, 0),
        )
        self.assertEqual(result.method, self.m.ExtractionMethod.FAILED)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("scann", result.error.lower())


if __name__ == "__main__":
    unittest.main()
