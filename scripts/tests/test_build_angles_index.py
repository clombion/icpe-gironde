"""Tests de build_angles_index.py — parsing front matter + freshness check."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.tests._loader import load_construire_fiches  # pour load pattern

# Chargement direct du module (pas besoin de _loader car stdlib-only)
import importlib.util
import sys

_MODULE_NAME = "build_angles_index"
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "build_angles_index.py"


def _load_module():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_MODULE_NAME}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


class TestParseYamlFrontMatter(unittest.TestCase):
    """Minimal front matter parser."""

    def setUp(self) -> None:
        self.m = _load_module()

    def test_extracts_title_question_caveat(self) -> None:
        text = (
            '---\n'
            'title: "Mon angle"\n'
            'question: "Pourquoi ?"\n'
            'caveat: "Attention."\n'
            '---\n'
            '\n# body\n'
        )
        fm = self.m.parse_yaml_front_matter(text)
        self.assertEqual(fm["title"], "Mon angle")
        self.assertEqual(fm["question"], "Pourquoi ?")
        self.assertEqual(fm["caveat"], "Attention.")

    def test_no_front_matter_returns_empty(self) -> None:
        fm = self.m.parse_yaml_front_matter("# Just markdown\n")
        self.assertEqual(fm, {})

    def test_unquoted_values(self) -> None:
        text = '---\ntitle: Simple title\n---\n'
        fm = self.m.parse_yaml_front_matter(text)
        self.assertEqual(fm["title"], "Simple title")


class TestBuildIndex(unittest.TestCase):
    """Test que build_index scanne les fichiers .md."""

    def setUp(self) -> None:
        self.m = _load_module()

    def test_scans_angles_dir(self) -> None:
        entries = self.m.build_index()
        self.assertGreaterEqual(len(entries), 5)
        titles = [e["title"] for e in entries]
        self.assertTrue(any("mise" in t.lower() for t in titles))

    def test_entries_have_required_keys(self) -> None:
        entries = self.m.build_index()
        for entry in entries:
            self.assertIn("file", entry)
            self.assertIn("title", entry)
            self.assertIn("question", entry)
            self.assertIn("caveat", entry)


class TestFreshnessCheck(unittest.TestCase):
    """Le index.json committé doit être à jour.

    Si ce test échoue, ça signifie qu'un .md a été édité sans
    re-runner ``python3 scripts/build_angles_index.py``.
    """

    def setUp(self) -> None:
        self.m = _load_module()

    def test_committed_index_matches_rebuild(self) -> None:
        index_path = self.m.INDEX_PATH
        if not index_path.exists():
            self.skipTest("index.json n'existe pas encore")
        committed = json.loads(index_path.read_text(encoding="utf-8"))
        rebuilt = self.m.build_index()
        self.assertEqual(
            committed,
            rebuilt,
            "index.json est périmé. Run : python3 scripts/build_angles_index.py",
        )


if __name__ == "__main__":
    unittest.main()
