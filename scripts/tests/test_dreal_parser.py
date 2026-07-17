"""Tests du parser gabarit DREAL et des helpers de rendu markdown.

Couvre :

- Extraction des métadonnées (Références, Code AIOT, date visite, établissement)
- Découpage en Contexte / Constats (robustesse aux duplications de marqueurs)
- Découpage en sous-sections 2-N)
- Extraction des fiches N° X
- Sérialisation YAML du front matter (roundtrip JSON)
- Rendu markdown final (structure, front matter en tête)
- Helpers : ``build_pages_url_markdown``, ``compute_sha256_str``

Les textes de test sont construits à la main pour rester focalisés et
explicites. Les intégrations avec de vrais PDFs sont en Phase 6.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from scripts.tests._loader import load_extractor


# Texte DREAL minimal mais réaliste : 4 marqueurs + métadonnées +
# sections Contexte / Constats + sous-sections 2-1 à 2-4 + fiches.
DREAL_SAMPLE = """Rapport de l'Inspection des installations classées Publié sur

ACME INDUSTRIES Zone industrielle 33000 BORDEAUX

Références : 24-001
Code AIOT : 0000123456
Visite d'inspection du 15/03/2024

1) Contexte

Paragraphe de contexte numéro un.
Paragraphe de contexte numéro deux.

2) Constats

2-1) Introduction

Le présent rapport rend compte de l'inspection.

2-2) Bilan synthétique des fiches de constats

Quelques lignes de bilan.

2-3) Ce qu'il faut retenir des fiches de constats

Trois points à retenir.

2-4) Fiches de constats

N° 1 : Premier point de contrôle
Référence réglementaire : AP du 01/01/2020
Constats : Rien à signaler sur ce point.
Type de suites proposées : Sans suite

N° 2 : Second point de contrôle
Référence réglementaire : AP du 01/01/2020, article 5
Constats : Non conformité mineure observée.
Type de suites proposées : Observation
"""


def _build_front_matter(m, **overrides):
    """Factory de FrontMatter pour tests de rendu."""
    base = {
        "source_pdf": "ACME_123456_2024-03-15_12345678901234.pdf",
        "source_sha256": "0" * 64,
        "source_bytes": 12345,
        "id_icpe": "123456",
        "nom_complet": "ACME INDUSTRIES",
        "siret": "12345678901234",
        "date_inspection": "2024-03-15",
        "identifiant_fichier": "abcDEF123",
        "url_source_georisques": (
            "https://www.georisques.gouv.fr/webappReport/ws/"
            "installations/inspection/abcDEF123"
        ),
        "url_pages": (
            "https://bononlouis-del.github.io/"
            "Les-ICPE-en-r-serve-naturelle-nationale/"
            "rapports-inspection/ACME_123456_2024-03-15_12345678901234.pdf"
        ),
        "extraction_method": "dreal_parser",
        "extraction_version": "0.1.0",
        "extracted_at": "2026-04-08T12:00:00",
    }
    base.update(overrides)
    return base


class DrealMetadataExtractionTests(unittest.TestCase):
    """Métadonnées du gabarit : références, code AIOT, date, établissement."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.sections = self.m.parse_dreal_sections(DREAL_SAMPLE)

    def test_references_extracted(self) -> None:
        self.assertEqual(self.sections.metadata.references, "24-001")

    def test_code_aiot_extracted(self) -> None:
        # Le code AIOT garde ses zéros de tête dans le front matter :
        # c'est l'id du document, pas l'id_icpe (qui lui est stripé).
        self.assertEqual(self.sections.metadata.code_aiot, "0000123456")

    def test_date_visite_extracted(self) -> None:
        self.assertEqual(self.sections.metadata.date_visite, "15/03/2024")

    def test_etablissement_flattened_to_single_line(self) -> None:
        # Le nom d'établissement couvre plusieurs lignes dans le PDF ;
        # la normalisation doit l'aplatir en une seule ligne pour le
        # titre markdown et le front matter YAML.
        etab = self.sections.metadata.etablissement
        self.assertNotIn("\n", etab)
        self.assertIn("ACME INDUSTRIES", etab)
        self.assertIn("33000 BORDEAUX", etab)


class DrealSectionSplitTests(unittest.TestCase):
    """Découpage Contexte / Constats et sous-sections 2-N."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.sections = self.m.parse_dreal_sections(DREAL_SAMPLE)

    def test_contexte_captures_both_paragraphs(self) -> None:
        self.assertIn("Paragraphe de contexte numéro un.", self.sections.contexte)
        self.assertIn("Paragraphe de contexte numéro deux.", self.sections.contexte)
        # Et ne déborde pas sur Constats.
        self.assertNotIn("2-1)", self.sections.contexte)

    def test_four_subsections_detected(self) -> None:
        self.assertEqual(len(self.sections.subsections), 4)

    def test_subsection_headings_preserved(self) -> None:
        headings = [h for h, _body in self.sections.subsections]
        self.assertEqual(
            headings,
            [
                "2-1) Introduction",
                "2-2) Bilan synthétique des fiches de constats",
                "2-3) Ce qu'il faut retenir des fiches de constats",
                "2-4) Fiches de constats",
            ],
        )

    def test_subsection_bodies_nonempty(self) -> None:
        # Chaque sous-section doit avoir du contenu après son titre.
        for heading, body in self.sections.subsections:
            self.assertTrue(
                body.strip(),
                f"sous-section {heading!r} vide",
            )


class DrealFicheExtractionTests(unittest.TestCase):
    """Fiches de constat N° X extraites de la sous-section 2-4."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.sections = self.m.parse_dreal_sections(DREAL_SAMPLE)

    def test_two_fiches_extracted(self) -> None:
        self.assertEqual(len(self.sections.fiches), 2)

    def test_fiche_fields(self) -> None:
        f1, f2 = self.sections.fiches
        self.assertEqual(f1.numero, "1")
        self.assertEqual(f1.titre, "Premier point de contrôle")
        self.assertIn("Rien à signaler", f1.body)
        self.assertEqual(f2.numero, "2")
        self.assertEqual(f2.titre, "Second point de contrôle")
        self.assertIn("Non conformité mineure", f2.body)

    def test_parse_fiches_constats_standalone(self) -> None:
        # La version autonome doit produire le même résultat que
        # l'extraction via parse_dreal_sections quand on lui donne le
        # texte de la sous-section 2-4.
        constats_body = next(
            body
            for heading, body in self.sections.subsections
            if heading.startswith("2-4)")
        )
        fiches = self.m.parse_fiches_constats(constats_body)
        self.assertEqual(len(fiches), 2)
        self.assertEqual(fiches[0].numero, "1")
        self.assertEqual(fiches[1].numero, "2")


class DuplicateMarkerRobustnessTests(unittest.TestCase):
    """``_split_contexte_constats`` doit choisir le 1er marqueur de chaque."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_first_markers_win_when_duplicates_present(self) -> None:
        # On construit un document avec un en-tête qui cite les
        # titres de section (sommaire) PUIS le vrai corps. L'ancre
        # début de ligne protège contre le sommaire embarqué sauf
        # si le sommaire est lui-même en début de ligne — on utilise
        # un préfixe non-début-de-ligne pour simuler un renvoi inline.
        text = (
            "Rapport de l'Inspection des installations classées\n"
            "Sommaire : voir 1) Contexte puis 2) Constats.\n"
            "Code AIOT : 0000012345\n"
            "\n"
            "1) Contexte\n"
            "Vrai contexte du rapport.\n"
            "\n"
            "2) Constats\n"
            "Vraies observations.\n"
        )
        sections = self.m.parse_dreal_sections(text)
        self.assertEqual(sections.contexte.strip(), "Vrai contexte du rapport.")


class FrontMatterRenderingTests(unittest.TestCase):
    """Sérialisation YAML du front matter : déterministe et valide JSON par ligne."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_output_is_wrapped_in_triple_dashes(self) -> None:
        fm = _build_front_matter(self.m)
        yaml = self.m.render_front_matter_yaml(fm)
        lines = yaml.split("\n")
        self.assertEqual(lines[0], "---")
        self.assertEqual(lines[-1], "---")

    def test_key_order_matches_typeddict_declaration(self) -> None:
        # Le front matter doit être stable run-à-run pour que les
        # snapshots ne bougent pas pour de mauvaises raisons.
        fm = _build_front_matter(self.m)
        yaml = self.m.render_front_matter_yaml(fm)
        # Extrait les clés dans l'ordre d'apparition dans le YAML.
        keys_in_yaml = [
            line.split(":", 1)[0]
            for line in yaml.split("\n")[1:-1]  # skip les ---
        ]
        expected = list(self.m.FrontMatter.__annotations__)
        self.assertEqual(keys_in_yaml, expected)

    def test_integer_field_unquoted(self) -> None:
        fm = _build_front_matter(self.m, source_bytes=99999)
        yaml = self.m.render_front_matter_yaml(fm)
        self.assertIn("source_bytes: 99999", yaml)
        self.assertNotIn('source_bytes: "99999"', yaml)

    def test_string_field_double_quoted(self) -> None:
        fm = _build_front_matter(self.m, nom_complet='NOM — AVEC "QUOTES"')
        yaml = self.m.render_front_matter_yaml(fm)
        # json.dumps échappe les " internes ; la valeur reste round-trippable.
        nom_line = next(
            line for line in yaml.split("\n") if line.startswith("nom_complet:")
        )
        value_str = nom_line[len("nom_complet: ") :]
        # Doit se désérialiser proprement comme JSON.
        self.assertEqual(json.loads(value_str), 'NOM — AVEC "QUOTES"')

    def test_unicode_preserved_not_escaped(self) -> None:
        # ensure_ascii=False → les accents restent en UTF-8 lisible.
        fm = _build_front_matter(self.m, nom_complet="BÈGLES ÉCOLE")
        yaml = self.m.render_front_matter_yaml(fm)
        self.assertIn("BÈGLES ÉCOLE", yaml)
        self.assertNotIn("\\u00c8", yaml)


class DrealMarkdownRenderTests(unittest.TestCase):
    """Rendu markdown complet à partir d'un parsing DREAL."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.sections = self.m.parse_dreal_sections(DREAL_SAMPLE)
        self.fm = _build_front_matter(self.m)
        self.md = self.m.render_dreal_markdown(self.sections, self.fm)

    def test_front_matter_at_start(self) -> None:
        self.assertTrue(self.md.startswith("---\n"))

    def test_main_title_contains_etablissement(self) -> None:
        self.assertIn("# Rapport d'inspection — ACME INDUSTRIES", self.md)

    def test_metadata_rendered_as_bold(self) -> None:
        self.assertIn("**Références** : 24-001", self.md)
        self.assertIn("**Code AIOT** : 0000123456", self.md)
        self.assertIn("**Visite d'inspection** : 15/03/2024", self.md)

    def test_contexte_section_present(self) -> None:
        self.assertIn("## 1) Contexte", self.md)
        self.assertIn("Paragraphe de contexte numéro un.", self.md)

    def test_constats_section_present(self) -> None:
        self.assertIn("## 2) Constats", self.md)

    def test_all_subsections_as_h3(self) -> None:
        for heading in (
            "### 2-1) Introduction",
            "### 2-2) Bilan synthétique des fiches de constats",
            "### 2-3) Ce qu'il faut retenir des fiches de constats",
            "### 2-4) Fiches de constats",
        ):
            self.assertIn(heading, self.md)

    def test_fiches_rendered_as_h4_with_numbers_and_titles(self) -> None:
        self.assertIn("#### Fiche N° 1 — Premier point de contrôle", self.md)
        self.assertIn("#### Fiche N° 2 — Second point de contrôle", self.md)

    def test_ends_with_single_newline(self) -> None:
        self.assertTrue(self.md.endswith("\n"))
        self.assertFalse(self.md.endswith("\n\n"))


class GenericAndFailedRenderTests(unittest.TestCase):
    """Rendus markdown des chemins non-DREAL."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.fm = _build_front_matter(self.m, extraction_method="pymupdf4llm_generic")

    def test_generic_prepends_front_matter(self) -> None:
        body = "# Document\n\nContenu libre.\n"
        md = self.m.render_generic_markdown(body, self.fm)
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("# Document", md)
        self.assertIn("Contenu libre.", md)

    def test_failed_includes_error_in_body(self) -> None:
        md = self.m.render_failed_markdown(self.fm, "fichier corrompu")
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("# Extraction impossible", md)
        self.assertIn("fichier corrompu", md)


class UrlHelperTests(unittest.TestCase):
    """``build_pages_url_markdown`` et ``compute_sha256_str``."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_pages_url_swaps_pdf_for_md(self) -> None:
        url = self.m.build_pages_url_markdown("ACME_123_2024-01-01_12345.pdf")
        self.assertTrue(url.endswith("ACME_123_2024-01-01_12345.md"))
        self.assertIn("rapports-inspection-markdown/", url)

    def test_pages_url_rejects_non_pdf(self) -> None:
        with self.assertRaises(ValueError):
            self.m.build_pages_url_markdown("foo.txt")

    def test_sha256_str_matches_stdlib(self) -> None:
        content = "Le contenu de test.\n"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertEqual(self.m.compute_sha256_str(content), expected)


if __name__ == "__main__":
    unittest.main()
