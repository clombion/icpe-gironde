"""Tests du manifeste append-only (Phase 4).

Le manifeste est le contrat de provenance entre le script et les
outils downstream. Une régression silencieuse ici (ligne ignorée,
dernière-entrée-qui-gagne cassée, schéma dérivé) se propage à tout
l'historique des extractions.

Ces tests tournent sans dépendance externe (stdlib uniquement) :
pas de pymupdf ni de jsonschema.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.tests._loader import load_extractor


def _entry(
    m,
    source_pdf: str,
    source_sha256: str,
    *,
    extraction_method: str = "dreal_parser",
    extraction_version: str | None = None,
    extracted_at: str = "2026-04-08T12:00:00",
) -> dict[str, str]:
    """Factory d'entrée de manifeste pour tests."""
    if extraction_version is None:
        extraction_version = m.EXTRACTION_VERSION
    return {
        "source_pdf": source_pdf,
        "source_sha256": source_sha256,
        "markdown_file": source_pdf.replace(".pdf", ".md"),
        "markdown_sha256": "b" * 64,
        "extraction_method": extraction_method,
        "extraction_version": extraction_version,
        "extracted_at": extracted_at,
    }


class ManifestRoundtripTests(unittest.TestCase):
    """Append → load → le résultat reconstitue les entrées."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = Path(self.tmpdir.name) / "_manifest.jsonl"

    def test_empty_manifest_returns_empty_dict(self) -> None:
        self.assertEqual(self.m.load_manifest(self.path), {})

    def test_single_append_then_load(self) -> None:
        entry = _entry(self.m, "foo.pdf", "a" * 64)
        self.m.append_manifest(self.path, entry)
        loaded = self.m.load_manifest(self.path)
        self.assertEqual(loaded, {"foo.pdf": entry})

    def test_multiple_entries_preserved(self) -> None:
        for i, name in enumerate(["a.pdf", "b.pdf", "c.pdf"]):
            self.m.append_manifest(
                self.path, _entry(self.m, name, str(i) * 64)
            )
        loaded = self.m.load_manifest(self.path)
        self.assertEqual(set(loaded), {"a.pdf", "b.pdf", "c.pdf"})

    def test_latest_entry_wins_on_duplicate_source_pdf(self) -> None:
        # Re-extraction du même PDF : la dernière entrée du fichier
        # fait autorité, les précédentes sont "ombragées" mais
        # restent visibles dans l'historique.
        self.m.append_manifest(
            self.path,
            _entry(self.m, "foo.pdf", "old" * 21 + "o"),  # 64 chars
        )
        self.m.append_manifest(
            self.path,
            _entry(self.m, "foo.pdf", "new" * 21 + "n"),
        )
        loaded = self.m.load_manifest(self.path)
        self.assertEqual(
            loaded["foo.pdf"]["source_sha256"],
            "new" * 21 + "n",
        )

    def test_malformed_lines_are_ignored(self) -> None:
        # Une ligne tronquée (ex. crash pendant append) ne doit pas
        # empoisonner la lecture des autres.
        entry = _entry(self.m, "ok.pdf", "a" * 64)
        self.m.append_manifest(self.path, entry)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("{this is not json\n")
            handle.write("\n")  # ligne vide tolérée
            handle.write('{"source_pdf": "bad.pdf"}\n')  # schéma partiel
        loaded = self.m.load_manifest(self.path)
        # "ok.pdf" passe. La ligne tronquée est ignorée. Le schéma
        # partiel passe quand même (contient source_pdf) mais son
        # sha256 manquant fera planter is_up_to_date downstream.
        self.assertIn("ok.pdf", loaded)


class IsUpToDateTests(unittest.TestCase):
    """Logique d'idempotence du pipeline."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_missing_entry_returns_false(self) -> None:
        self.assertFalse(self.m.is_up_to_date({}, "foo.pdf", "a" * 64))

    def test_matching_sha_and_version_returns_true(self) -> None:
        manifest = {"foo.pdf": _entry(self.m, "foo.pdf", "a" * 64)}
        self.assertTrue(self.m.is_up_to_date(manifest, "foo.pdf", "a" * 64))

    def test_wrong_sha_returns_false(self) -> None:
        manifest = {"foo.pdf": _entry(self.m, "foo.pdf", "a" * 64)}
        self.assertFalse(self.m.is_up_to_date(manifest, "foo.pdf", "b" * 64))

    def test_old_extraction_version_returns_false(self) -> None:
        # Bump de version → tout est invalidé, on ré-extrait.
        manifest = {
            "foo.pdf": _entry(
                self.m,
                "foo.pdf",
                "a" * 64,
                extraction_version="0.0.1-obsolete",
            )
        }
        self.assertFalse(self.m.is_up_to_date(manifest, "foo.pdf", "a" * 64))


class ComputeSha256Tests(unittest.TestCase):
    """``compute_sha256`` (streaming) doit matcher ``hashlib`` direct."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_sha256_matches_stdlib_for_small_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"contenu de test")
            tmp_path = Path(tmp.name)
        try:
            expected = hashlib.sha256(b"contenu de test").hexdigest()
            self.assertEqual(self.m.compute_sha256(tmp_path), expected)
        finally:
            tmp_path.unlink()

    def test_sha256_matches_stdlib_for_large_file(self) -> None:
        # Dépasse la taille de chunk (64 KiB) pour exercer la boucle.
        payload = b"x" * (200 * 1024)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        try:
            expected = hashlib.sha256(payload).hexdigest()
            self.assertEqual(self.m.compute_sha256(tmp_path), expected)
        finally:
            tmp_path.unlink()


class BuildManifestEntryTests(unittest.TestCase):
    """``build_manifest_entry`` compose proprement front matter + markdown."""

    def setUp(self) -> None:
        self.m = load_extractor()

    def test_entry_fields_come_from_front_matter(self) -> None:
        fm = {
            "source_pdf": "foo.pdf",
            "source_sha256": "a" * 64,
            "source_bytes": 100,
            "id_icpe": "123",
            "nom_complet": "ACME",
            "siret": "",
            "date_inspection": "",
            "identifiant_fichier": "xyz",
            "url_source_georisques": "https://x",
            "url_pages": "https://y.pdf",
            "extraction_method": "dreal_parser",
            "extraction_version": "0.1.0",
            "extracted_at": "2026-04-08T12:00:00",
        }
        md_path = Path("/tmp/foo.md")
        entry = self.m.build_manifest_entry(fm, "# hello\n", md_path)
        self.assertEqual(entry["source_pdf"], "foo.pdf")
        self.assertEqual(entry["source_sha256"], "a" * 64)
        self.assertEqual(entry["markdown_file"], "foo.md")
        self.assertEqual(entry["extraction_method"], "dreal_parser")
        self.assertEqual(entry["extraction_version"], "0.1.0")
        # markdown_sha256 est calculé à partir du texte fourni.
        expected_sha = hashlib.sha256(b"# hello\n").hexdigest()
        self.assertEqual(entry["markdown_sha256"], expected_sha)


if __name__ == "__main__":
    unittest.main()
