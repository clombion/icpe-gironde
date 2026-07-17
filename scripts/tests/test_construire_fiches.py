"""Tests de construire_fiches.py — parsing des champs labélisés et build du pivot.

Tous les tests de cette classe sont stdlib-only (pas de duckdb/jsonschema
nécessaire) sauf ceux marqués skipUnless. Le module est chargé via
_loader.py qui gère le lazy import des deps lourdes.
"""

from __future__ import annotations

import unittest

from scripts.tests._loader import load_construire_fiches


class TestParseFicheLabeledFields(unittest.TestCase):
    """Parse les champs structurés du body d'une fiche DREAL."""

    def setUp(self) -> None:
        self.m = load_construire_fiches()

    def test_all_fields_extracted(self) -> None:
        body = (
            "Référence réglementaire : AP du 01/01/2020, article 5\n"
            "Thème(s) : Déchets, Eau\n"
            "Point de contrôle déjà contrôlé : Non\n"
            "Prescription contrôlée :\n"
            "L'exploitant doit vérifier le bon état des réseaux.\n"
            "Constats : Le réseau a été vérifié le 12/03/2024.\n"
            "Type de suites proposées : Sans suite\n"
            "Proposition de suites : Sans objet"
        )
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertEqual(fields["reference_reglementaire"], "AP du 01/01/2020, article 5")
        self.assertEqual(fields["theme"], "Déchets, Eau")
        self.assertEqual(fields["deja_controle"], "Non")
        self.assertIn("bon état des réseaux", fields["prescription"])
        self.assertIn("vérifié le 12/03/2024", fields["constats_body"])
        self.assertEqual(fields["type_suite"], "Sans suite")
        self.assertEqual(fields["proposition_suite"], "Sans objet")

    def test_missing_field_returns_empty_string(self) -> None:
        body = "Constats : Constat simple\nType de suites proposées : Avec suites"
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertEqual(fields["reference_reglementaire"], "")
        self.assertEqual(fields["theme"], "")
        self.assertEqual(fields["constats_body"], "Constat simple")
        self.assertEqual(fields["type_suite"], "Avec suites")

    def test_double_spaced_labels_matched(self) -> None:
        # Le gabarit DREAL produit parfois des espaces multiples
        body = "Type  de  suites  proposées : Mise en demeure"
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertEqual(fields["type_suite"], "Mise en demeure")

    def test_multiline_prescription(self) -> None:
        body = (
            "Prescription contrôlée :\n"
            "Ligne 1 de la prescription.\n"
            "Ligne 2 de la prescription.\n"
            "Constats : Le constat."
        )
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertIn("Ligne 1", fields["prescription"])
        self.assertIn("Ligne 2", fields["prescription"])
        self.assertEqual(fields["constats_body"], "Le constat.")

    def test_trailing_page_number_stripped(self) -> None:
        body = "Type de suites proposées : Sans suite\n5\n"
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertEqual(fields["type_suite"], "Sans suite")

    def test_trailing_pipe_stripped(self) -> None:
        body = "Type de suites proposées : Avec suites\n|\n"
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertEqual(fields["type_suite"], "Avec suites")

    def test_deja_controle_values(self) -> None:
        for value in ("Oui", "Non", "Sans Objet"):
            body = f"Point de contrôle déjà contrôlé : {value}"
            fields = self.m.parse_fiche_labeled_fields(body)
            self.assertEqual(fields["deja_controle"], value)

    def test_empty_body_returns_all_empty(self) -> None:
        fields = self.m.parse_fiche_labeled_fields("")
        for key, val in fields.items():
            self.assertEqual(val, "", f"field {key} should be empty for empty body")

    def test_theme_with_comma_separated_values(self) -> None:
        body = "Thème(s) : Risques accidentels, Situation administrative, Rubrique 1435"
        fields = self.m.parse_fiche_labeled_fields(body)
        self.assertIn("Risques accidentels", fields["theme"])
        self.assertIn("Rubrique 1435", fields["theme"])


class TestBuildFicheId(unittest.TestCase):
    """Le fiche_id est déterministe et unique."""

    def setUp(self) -> None:
        self.m = load_construire_fiches()

    def test_structured_fiche_uses_seq_index(self) -> None:
        fid = self.m.build_fiche_id("ACME_123_2024-01-01_12345678901234", 3)
        self.assertEqual(fid, "ACME_123_2024-01-01_12345678901234_f03")

    def test_prose_row_uses_prose_suffix(self) -> None:
        fid = self.m.build_fiche_id("ACME_123_2024-01-01_12345678901234", None)
        self.assertEqual(fid, "ACME_123_2024-01-01_12345678901234_prose")

    def test_zero_padded_to_two_digits(self) -> None:
        self.assertTrue(self.m.build_fiche_id("stem", 1).endswith("_f01"))
        self.assertTrue(self.m.build_fiche_id("stem", 12).endswith("_f12"))


class TestStripFrontMatter(unittest.TestCase):
    """Retire le bloc YAML en tête d'un markdown."""

    def setUp(self) -> None:
        self.m = load_construire_fiches()

    def test_strip_front_matter(self) -> None:
        md = "---\nkey: value\n---\n\n# Body\n\nContent."
        result = self.m.strip_front_matter(md)
        self.assertEqual(result, "# Body\n\nContent.")

    def test_no_front_matter_returns_text(self) -> None:
        md = "# Just a heading\n\nContent."
        self.assertEqual(self.m.strip_front_matter(md), md)

    def test_unclosed_front_matter_returns_text(self) -> None:
        md = "---\nkey: value\nno closing"
        self.assertEqual(self.m.strip_front_matter(md), md)


if __name__ == "__main__":
    unittest.main()
