"""Tests de ``clean_text`` et des helpers de nettoyage de texte brut.

Ces fonctions sont la porte d'entrée du pipeline : si elles
mangeaient du contenu légitime ou laissaient passer des artefacts,
tout le reste serait affecté. Un bug ici se propage à 1782 fichiers
markdown — on teste pointilleusement.
"""

from __future__ import annotations

import unittest

from scripts.tests._loader import load_extractor


class CleanTextTests(unittest.TestCase):
    """Vérifie ``clean_text`` sur les artefacts courants des PDFs DREAL."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_replaces_form_feed_with_newline(self) -> None:
        # Pymupdf insère parfois \f entre les pages ; on veut un \n
        # pour préserver les paragraphes sans créer de caractères
        # invisibles.
        input_text = "page 1 contenu\fpage 2 contenu"
        output = self.m.clean_text(input_text)
        self.assertNotIn("\f", output)
        self.assertIn("page 1 contenu", output)
        self.assertIn("page 2 contenu", output)

    def test_normalizes_non_breaking_spaces(self) -> None:
        # \xa0 (nbsp) est invisible à l'œil mais casse les regex de
        # parsing qui cherchent des espaces standard.
        input_text = "champ\xa0: valeur"
        self.assertEqual(self.m.clean_text(input_text), "champ : valeur")

    def test_strips_soft_hyphens(self) -> None:
        # \u00ad (soft hyphen) apparaît dans les PDFs avec coupures
        # de mots et pollue les snapshots.
        input_text = "ins\u00adpec\u00adtion"
        self.assertEqual(self.m.clean_text(input_text), "inspection")

    def test_collapses_excess_blank_lines(self) -> None:
        input_text = "paragraph 1\n\n\n\n\nparagraph 2"
        self.assertEqual(
            self.m.clean_text(input_text),
            "paragraph 1\n\nparagraph 2",
        )

    def test_preserves_single_blank_line_between_paragraphs(self) -> None:
        # On garde la structure en paragraphes : un saut simple reste
        # un saut simple, deux sauts (blank line) restent deux sauts.
        input_text = "paragraph 1\n\nparagraph 2\nline inside same paragraph"
        output = self.m.clean_text(input_text)
        self.assertEqual(
            output,
            "paragraph 1\n\nparagraph 2\nline inside same paragraph",
        )

    def test_strips_trailing_whitespace_per_line(self) -> None:
        input_text = "line with trailing   \nnext line\t\t\nblank     "
        output = self.m.clean_text(input_text)
        # Chaque ligne est nettoyée ; la fin du document aussi.
        self.assertEqual(output, "line with trailing\nnext line\nblank")

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        input_text = "\n\n  content  \n\n"
        self.assertEqual(self.m.clean_text(input_text), "content")

    def test_empty_string_stays_empty(self) -> None:
        self.assertEqual(self.m.clean_text(""), "")

    def test_whitespace_only_becomes_empty(self) -> None:
        # Un PDF scan produit parfois juste des caractères blancs.
        self.assertEqual(self.m.clean_text("   \n\n\n\t   "), "")


if __name__ == "__main__":
    unittest.main()
