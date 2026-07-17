#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
# ]
# ///
"""
extract_corpus.py — Extrait les fiches ICPE vers corpus-all/ pour le tagging LLM.

Lit fiches.parquet, exclut les fiches vides (constats_body NULL ou ≤10 chars),
et écrit un fichier .txt par fiche dans corpus-all/ + un manifest CSV.

Le slug est dérivé du fiche_id : lowercase, accents strippés, non-alnum supprimés
(sauf tirets). Le manifest préserve le mapping fiche_id↔slug pour le join.

Usage :
  uv run scripts/extract_corpus.py
"""

from __future__ import annotations

import csv
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import CARTE_FICHES_PARQUET, PROJECT_ROOT  # noqa: E402

CORPUS_DIR = PROJECT_ROOT / "corpus-all"
MANIFEST_PATH = PROJECT_ROOT / "outputs-fiches" / "manifest-all.csv"

MANIFEST_COLUMNS = [
    "fiche_id",
    "slug",
    "filename",
    "word_count",
    "date_inspection",
    "type_suite",
    "theme",
    "regime_icpe",
    "categorie_seveso",
    "nom_commune",
    "id_icpe",
    "source_pdf",
]


def fiche_id_to_slug(fiche_id: str) -> str:
    """Derive a filesystem-safe slug from fiche_id.

    Lowercase, strip accents, remove non-alphanumeric except hyphens.
    """
    slug = fiche_id.lower()
    # Decompose unicode, strip combining marks (accents)
    slug = unicodedata.normalize("NFKD", slug)
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    # Keep only alphanumeric and hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug


def main() -> int:
    if not CARTE_FICHES_PARQUET.exists():
        print(
            f"[error] {CARTE_FICHES_PARQUET} absent — lancer construire_fiches.py d'abord",
            file=sys.stderr,
        )
        return 1

    import duckdb

    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"""
        SELECT
            fiche_id,
            constats_body,
            prescription,
            date_inspection,
            type_suite,
            theme,
            regime_icpe,
            categorie_seveso,
            nom_commune,
            id_icpe,
            source_pdf
        FROM '{CARTE_FICHES_PARQUET}'
        WHERE constats_body IS NOT NULL
          AND LENGTH(TRIM(constats_body)) > 10
        ORDER BY fiche_id
        """
    ).fetchall()
    con.close()
    print(f"[read] {len(rows)} fiches à extraire (hors vides)")

    # Create output dirs
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Track slugs for uniqueness check
    seen_slugs: dict[str, str] = {}
    manifest_rows: list[dict[str, str]] = []

    for row in rows:
        (
            fiche_id,
            constats_body,
            prescription,
            date_inspection,
            type_suite,
            theme,
            regime_icpe,
            categorie_seveso,
            nom_commune,
            id_icpe,
            source_pdf,
        ) = row

        slug = fiche_id_to_slug(fiche_id)

        # Uniqueness check
        if slug in seen_slugs:
            print(
                f"[error] Duplicate slug '{slug}' for fiche_id '{fiche_id}' "
                f"(already used by '{seen_slugs[slug]}')",
                file=sys.stderr,
            )
            return 1
        seen_slugs[slug] = fiche_id

        # Write corpus file
        filename = f"{slug}.txt"
        body = f"Constats body:\n{constats_body}"
        if prescription and prescription.strip():
            body += f"\n\nPrescription:\n{prescription}"

        (CORPUS_DIR / filename).write_text(body, encoding="utf-8")

        word_count = len(body.split())

        manifest_rows.append(
            {
                "fiche_id": fiche_id,
                "slug": slug,
                "filename": filename,
                "word_count": str(word_count),
                "date_inspection": date_inspection or "",
                "type_suite": type_suite or "",
                "theme": theme or "",
                "regime_icpe": regime_icpe or "",
                "categorie_seveso": categorie_seveso or "",
                "nom_commune": nom_commune or "",
                "id_icpe": id_icpe or "",
                "source_pdf": source_pdf or "",
            }
        )

    # Write manifest
    with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"[write] {len(manifest_rows)} fichiers dans {CORPUS_DIR.name}/")
    print(f"[write] {MANIFEST_PATH.name} ({len(manifest_rows)} lignes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
