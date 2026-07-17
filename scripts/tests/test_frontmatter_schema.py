"""Tests du JSON Schema du front matter et de sa validation.

Ces tests nécessitent ``jsonschema`` (déclaré dans le PEP 723 du
script principal). Lancement :

    uv run -m unittest discover scripts/tests

Ou directement :

    uv run -m unittest scripts.tests.test_frontmatter_schema

Le but est triple :

1. Vérifier que le fichier ``scripts/schemas/markdown_frontmatter.json``
   est un JSON Schema draft-07 syntaxiquement correct.
2. Vérifier qu'un ``FrontMatter`` construit par notre propre code
   passe la validation — protection contre une dérive entre le
   ``TypedDict`` et le schéma.
3. Vérifier que des violations courantes (champ manquant, type
   incorrect, enum invalide) sont bien détectées et levées comme
   ``RuntimeError`` par notre wrapper.
"""

from __future__ import annotations

import json
import unittest

from scripts.tests._loader import load_extractor

try:
    import jsonschema  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    JSONSCHEMA_AVAILABLE = False
else:
    JSONSCHEMA_AVAILABLE = True


class SchemaFileTests(unittest.TestCase):
    """Le fichier schema est valide et a la shape attendue.

    Ce bloc n'a PAS besoin de ``jsonschema`` : on lit juste le fichier
    JSON et on vérifie sa structure. Ça garantit que le schéma existe
    et est cohérent même sur un environnement stdlib-only.
    """

    def setUp(self) -> None:
        self.m = load_extractor()
        self.schema = self.m.load_schema()

    def test_schema_has_draft07_metaschema(self) -> None:
        self.assertEqual(
            self.schema.get("$schema"),
            "http://json-schema.org/draft-07/schema#",
        )

    def test_schema_additional_properties_false(self) -> None:
        # Garde-fou critique : empêche le front matter d'accumuler
        # des clés au fil du temps sans validation explicite.
        self.assertFalse(self.schema.get("additionalProperties", True))

    def test_schema_required_matches_typeddict(self) -> None:
        # Toutes les clés du TypedDict doivent être requises dans le
        # schéma — sinon un bug dans build_front_matter_from_csv
        # passerait silencieusement.
        required = set(self.schema.get("required", []))
        typeddict_keys = set(self.m.FrontMatter.__annotations__)
        self.assertEqual(required, typeddict_keys)

    def test_schema_properties_match_typeddict(self) -> None:
        schema_props = set(self.schema.get("properties", {}).keys())
        typeddict_keys = set(self.m.FrontMatter.__annotations__)
        self.assertEqual(schema_props, typeddict_keys)

    def test_extraction_method_enum_covers_all_success_members(self) -> None:
        # Le schéma doit accepter toutes les méthodes définies par
        # ExtractionMethod. Un nouvel ajout à l'enum doit déclencher
        # l'ajout correspondant dans le schéma.
        method_prop = self.schema["properties"]["extraction_method"]
        schema_enum = set(method_prop.get("enum", []))
        enum_members = {e.value for e in self.m.ExtractionMethod}
        self.assertEqual(
            schema_enum,
            enum_members,
            "le champ 'extraction_method' du schéma est désynchronisé "
            "de l'enum ExtractionMethod",
        )


def _valid_front_matter(m) -> dict[str, object]:
    """Factory : un FrontMatter qui passe la validation."""
    return {
        "source_pdf": "ACME_123_2024-01-01_12345678901234.pdf",
        "source_sha256": "0" * 64,
        "source_bytes": 100,
        "id_icpe": "123",
        "nom_complet": "ACME",
        "siret": "12345678901234",
        "date_inspection": "2024-01-01",
        "identifiant_fichier": "abc",
        "url_source_georisques": (
            "https://www.georisques.gouv.fr/webappReport/ws/"
            "installations/inspection/abc"
        ),
        "url_pages": "https://example.com/x.pdf",
        "extraction_method": "dreal_parser",
        "extraction_version": "0.1.0",
        "extracted_at": "2026-04-08T12:00:00",
    }


@unittest.skipUnless(JSONSCHEMA_AVAILABLE, "jsonschema non installé")
class FrontMatterValidationTests(unittest.TestCase):
    """Notre wrapper ``validate_front_matter_against_schema`` lève correctement."""

    def setUp(self) -> None:
        self.m = load_extractor()
        self.schema = self.m.load_schema()

    def test_valid_front_matter_passes(self) -> None:
        fm = _valid_front_matter(self.m)
        # Doit ne rien lever.
        self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_missing_required_field_fails(self) -> None:
        fm = _valid_front_matter(self.m)
        del fm["source_sha256"]
        with self.assertRaises(RuntimeError) as ctx:
            self.m.validate_front_matter_against_schema(fm, self.schema)
        self.assertIn("source_sha256", str(ctx.exception))

    def test_wrong_type_fails(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["source_bytes"] = "not an int"
        with self.assertRaises(RuntimeError):
            self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_unknown_extraction_method_fails(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["extraction_method"] = "not_a_method"
        with self.assertRaises(RuntimeError):
            self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_siret_wrong_length_fails(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["siret"] = "12345"  # ni vide ni 14 chiffres
        with self.assertRaises(RuntimeError):
            self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_empty_siret_is_allowed(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["siret"] = ""
        self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_empty_date_inspection_is_allowed(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["date_inspection"] = ""
        self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_invalid_sha256_format_fails(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["source_sha256"] = "not-a-hash"
        with self.assertRaises(RuntimeError):
            self.m.validate_front_matter_against_schema(fm, self.schema)

    def test_additional_property_rejected(self) -> None:
        fm = _valid_front_matter(self.m)
        fm["extra_unexpected_key"] = "should fail"
        with self.assertRaises(RuntimeError):
            self.m.validate_front_matter_against_schema(fm, self.schema)


class ParseFrontMatterBlockTests(unittest.TestCase):
    """Round-trip : render → parse → valide.

    Seul ``test_roundtrip_passes_schema_validation`` dépend de
    ``jsonschema`` ; les 3 autres tests sont de la stdlib pure.
    """

    def setUp(self) -> None:
        self.m = load_extractor()
        self.schema = self.m.load_schema()

    def test_roundtrip_preserves_all_fields(self) -> None:
        original = _valid_front_matter(self.m)
        yaml = self.m.render_front_matter_yaml(original)
        # Ajoute un corps markdown minimal.
        markdown = yaml + "\n\n# Corps\n"
        parsed = self.m._parse_front_matter_block(markdown)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed, original)

    @unittest.skipUnless(JSONSCHEMA_AVAILABLE, "jsonschema non installé")
    def test_roundtrip_passes_schema_validation(self) -> None:
        original = _valid_front_matter(self.m)
        yaml = self.m.render_front_matter_yaml(original)
        markdown = yaml + "\n\n# Corps\n"
        parsed = self.m._parse_front_matter_block(markdown)
        assert parsed is not None
        self.m.validate_front_matter_against_schema(parsed, self.schema)

    def test_missing_front_matter_returns_none(self) -> None:
        # Pas de bloc --- en tête → None, et run_validation compte
        # ça comme une erreur.
        self.assertIsNone(
            self.m._parse_front_matter_block("# juste un titre\ncontenu")
        )

    def test_unclosed_front_matter_returns_none(self) -> None:
        self.assertIsNone(
            self.m._parse_front_matter_block("---\nkey: \"value\"\ncorps")
        )


if __name__ == "__main__":
    unittest.main()
