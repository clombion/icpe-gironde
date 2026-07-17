"""Tests pour les scripts du pipeline Géorisques / rapports d'inspection.

Lancement :

    python3 -m unittest discover scripts/tests

Les tests sont répartis en deux niveaux :

- Tests unitaires purs (Phase 2) : ``test_sanitize_and_clean``,
  ``test_classification``, ``test_dreal_parser``. Aucune dépendance
  externe, tournent avec le Python du système.

- Tests d'intégration (Phase 6) : ``test_integration``,
  ``test_frontmatter_schema``. Nécessitent pymupdf, pymupdf4llm et
  jsonschema. À lancer via ``uv run -m unittest discover scripts/tests``
  après avoir installé les dépendances du PEP 723 du script principal.

Le helper ``_load_module`` de ``conftest`` charge le script extracteur
comme module sans qu'il soit installable — pratique pour un script
autonome qui n'a pas de ``pyproject.toml``.
"""
