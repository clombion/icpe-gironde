"""Tests de ``needs_ocr``, ``is_dreal_template`` et ``classify_text``.

Ces trois fonctions constituent la gare de triage du pipeline :
chaque PDF passe par elles pour choisir son chemin d'extraction. Un
faux-négatif sur ``is_dreal_template`` envoie un rapport DREAL vers le
fallback générique (perte de structure) ; un faux-positif fait
crasher le parser sur un document qui n'a pas la structure attendue.
"""

from __future__ import annotations

import unittest

from scripts.tests._loader import load_extractor


# Bout de texte contenant tous les marqueurs DREAL attendus et rien
# d'autre — le minimum structurel pour que ``is_dreal_template``
# retourne True.
DREAL_MINIMAL = (
    "Rapport de l'Inspection des installations classées\n"
    "1) Contexte\n"
    "2) Constats\n"
    "Code AIOT : 0000012345\n"
)

# Texte qui ressemble à un DREAL mais avec 3 marqueurs sur 4 — le
# classifieur doit le traiter comme générique.
DREAL_INCOMPLETE = (
    "Rapport de l'Inspection des installations classées\n"
    "1) Contexte\n"
    "2) Constats\n"
    # Pas de Code AIOT
)


class NeedsOcrTests(unittest.TestCase):
    """Détection des scans par longueur minimale."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_empty_text_needs_ocr(self) -> None:
        self.assertTrue(self.m.needs_ocr(""))

    def test_whitespace_only_needs_ocr(self) -> None:
        # Certains scans produisent juste des blancs via pymupdf.
        self.assertTrue(self.m.needs_ocr("   \n\n  "))

    def test_below_threshold_needs_ocr(self) -> None:
        # Strictement sous SCAN_MIN_CHARS (32) après strip.
        short = "a" * 20
        self.assertTrue(self.m.needs_ocr(short))

    def test_above_threshold_does_not_need_ocr(self) -> None:
        # Au-dessus du seuil : on ne route pas vers OCR.
        long = "a" * 200
        self.assertFalse(self.m.needs_ocr(long))

    def test_strip_is_applied_before_length_check(self) -> None:
        # "Plein" de whitespace ne compte pas : seul le contenu réel
        # décide si OCR est nécessaire.
        padded = "\n\n\n" + "a" * 20 + "\n\n\n"
        self.assertTrue(self.m.needs_ocr(padded))


class IsDrealTemplateTests(unittest.TestCase):
    """Détection du gabarit DREAL par présence des 4 marqueurs."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_minimal_dreal_markers_match(self) -> None:
        self.assertTrue(self.m.is_dreal_template(DREAL_MINIMAL))

    def test_three_out_of_four_markers_fails(self) -> None:
        # Tous-ou-rien : 3/4 ne suffit pas pour prétendre au gabarit.
        self.assertFalse(self.m.is_dreal_template(DREAL_INCOMPLETE))

    def test_empty_text_fails(self) -> None:
        self.assertFalse(self.m.is_dreal_template(""))

    def test_generic_letter_fails(self) -> None:
        # Un courrier administratif sans aucun marqueur DREAL.
        letter = (
            "Madame, Monsieur,\n\n"
            "Suite à votre demande du 12 mars, je vous transmets...\n"
        )
        self.assertFalse(self.m.is_dreal_template(letter))

    def test_markers_can_be_non_contiguous(self) -> None:
        # Les marqueurs peuvent être éparpillés dans le document,
        # avec du contenu entre eux — seul leur présence compte.
        spread = (
            "Rapport de l'Inspection des installations classées\n"
            "blah blah blah entre les marqueurs\n"
            "1) Contexte\n"
            "encore du contenu\n"
            "2) Constats\n"
            "beaucoup plus de contenu\n"
            "Code AIOT : 0000012345\n"
        )
        self.assertTrue(self.m.is_dreal_template(spread))


class ClassifyTextTests(unittest.TestCase):
    """Routage en méthode d'extraction hors cas OCR."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_dreal_text_routes_to_dreal_parser(self) -> None:
        result = self.m.classify_text(DREAL_MINIMAL)
        self.assertEqual(result, self.m.ExtractionMethod.DREAL_PARSER)

    def test_generic_text_routes_to_pymupdf4llm(self) -> None:
        generic = "# Rapport libre\n\nContenu sans marqueurs DREAL.\n"
        result = self.m.classify_text(generic)
        self.assertEqual(
            result, self.m.ExtractionMethod.PYMUPDF4LLM_GENERIC
        )

    def test_classify_never_returns_ocr_methods(self) -> None:
        # Contract : classify_text ne s'occupe pas de l'OCR ; il retourne
        # DREAL_PARSER ou PYMUPDF4LLM_GENERIC. Jamais un OCR_THEN_*
        # et jamais FAILED.
        for text in ("", "x", DREAL_MINIMAL, "aléatoire"):
            method = self.m.classify_text(text)
            self.assertIn(
                method,
                {
                    self.m.ExtractionMethod.DREAL_PARSER,
                    self.m.ExtractionMethod.PYMUPDF4LLM_GENERIC,
                },
                f"classify_text({text!r}) a retourné {method}",
            )


if __name__ == "__main__":
    unittest.main()
