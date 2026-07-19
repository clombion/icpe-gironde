"""Tests de build_public_angles.py — figures réconciliées + garde-fou oracle.

Le script lit la base réelle (carte/data/fiches.sqlite) et échoue sur tout
écart aux oracles ; ces tests vérifient (a) que le build passe et produit les
figures attendues, et (b) que le garde-fou ``check`` échoue bien sur un écart
(sinon la réconciliation serait décorative — leçon BUG-007/BUG-008).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

_MODULE_NAME = "build_public_angles"
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "build_public_angles.py"


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


class TestOracleGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.m = _load_module()

    def test_check_passes_on_match(self) -> None:
        name = next(iter(self.m.ORACLES))
        self.assertEqual(self.m.check(name, self.m.ORACLES[name]), self.m.ORACLES[name])

    def test_check_fails_on_mismatch(self) -> None:
        """Le garde-fou doit lever — sinon une figure fausse partirait en ligne."""
        name = next(iter(self.m.ORACLES))
        with self.assertRaises(SystemExit):
            self.m.check(name, self.m.ORACLES[name] + 1)


class TestBuildFigures(unittest.TestCase):
    """Lance le build réel et vérifie l'artefact contre les figures connues."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_module()
        if not cls.m.SQLITE.exists():
            raise unittest.SkipTest(f"{cls.m.SQLITE} absent")
        cls.assertEqual0 = cls.m.main()  # écrit enquete/angles.json
        cls.doc = json.loads(cls.m.OUT.read_text(encoding="utf-8"))

    def test_build_succeeds(self) -> None:
        self.assertEqual(self.assertEqual0, 0)

    def test_corpus(self) -> None:
        self.assertEqual(self.doc["corpus"], {"tagged": 10514, "untagged": 478, "total": 10992})

    def test_routine(self) -> None:
        a = next(x for x in self.doc["angles"] if x["id"] == "routine")
        self.assertEqual(a["big"]["value"], 7096)
        self.assertEqual(a["big"]["of"], 10514)
        self.assertEqual(a["detail"]["m08"], 4539)

    def test_risques_averes(self) -> None:
        a = next(x for x in self.doc["angles"] if x["id"] == "risques-averes")
        self.assertEqual(a["big"]["value"], 743)
        by = {b["code"]: b["n"] for b in a["bars"]}
        self.assertEqual((by["D01"], by["D04"], by["D08"]), (202, 161, 117))

    def test_recidivistes(self) -> None:
        a = next(x for x in self.doc["angles"] if x["id"] == "recidivistes")
        sig = {s["code"]: s["n"] for s in a["signals"]}
        self.assertEqual((sig["T5"], sig["T7"], sig["M04"]), (114, 110, 772))
        self.assertTrue(a["named"], "classement nommé non vide")
        self.assertTrue(all(x["nom"] for x in a["named"]))

    def test_creuser_links_are_explorer_hashes(self) -> None:
        for a in self.doc["angles"]:
            self.assertIn("explorer.html#", a["creuser"])


if __name__ == "__main__":
    unittest.main()
