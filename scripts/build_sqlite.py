#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
# ]
# ///
"""
build_sqlite.py — Exporte fiches.parquet vers fiches.sqlite pour sql.js.

Le parquet est la source de vérité du pipeline (colonnaire, compressé
ZSTD, 12 MB). Le SQLite est un artifact de build consommé par le
frontend via sql.js (~1 MB WASM). Le browser télécharge le SQLite
complet (~10 MB gzippé) et l'ouvre en mémoire.

Toutes les colonnes sont exportées en TEXT sauf ``regions`` (JSON
complexe non utilisé par sql.js — le frontend parse le JSON dans le
parquet si nécessaire via le champ ``url_pages`` + bbox côté PDF.js).

Des indexes sont créés sur les colonnes utilisées par les filtres et
la recherche pour accélérer les requêtes synchrones de sql.js.

Usage :
  uv run scripts/build_sqlite.py

Dépendances : duckdb (PEP 723) pour lire le parquet.
L'écriture SQLite utilise le module stdlib ``sqlite3``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import CARTE_DATA_DIR, CARTE_FICHES_PARQUET, CARTE_FICHES_SQLITE  # noqa: E402

FICHES_TAGS_PARQUET = CARTE_DATA_DIR / "fiches-tags.parquet"

# Colonnes sur lesquelles créer un index (utilisées par search/filter).
INDEX_COLUMNS = [
    "fiche_id",
    "nom_complet",
    "nom_commune",
    "type_suite",
    "date_inspection",
    "regime_icpe",
    "categorie_seveso",
    "gravity",
    "trajectory",
    "dynamic",
]


MULTI_LABEL_AXES = ("domains", "mechanisms", "modifiers")


def _build_long_tag_table(scon, columns: list[str]) -> None:
    """Déplie les colonnes JSON multi-label en table longue
    ``fiche_tags(fiche_id, axis, code)``, indexée sur ``(axis, code)``."""
    present = [a for a in MULTI_LABEL_AXES if a in columns]
    if not present:
        return
    scon.execute(
        "CREATE TABLE fiche_tags (fiche_id TEXT, axis TEXT, code TEXT)"
    )
    long_rows: list[tuple[str, str, str]] = []
    for axis in present:
        for (fiche_id, payload) in scon.execute(
            f'SELECT fiche_id, "{axis}" FROM fiches WHERE "{axis}" IS NOT NULL'
        ).fetchall():
            try:
                codes = json.loads(payload) if payload else []
            except (json.JSONDecodeError, TypeError):
                continue
            long_rows.extend((fiche_id, axis, code) for code in codes)
    scon.executemany("INSERT INTO fiche_tags VALUES (?, ?, ?)", long_rows)
    scon.execute("CREATE INDEX idx_fiche_tags_code ON fiche_tags(axis, code)")
    scon.execute("CREATE INDEX idx_fiche_tags_fiche ON fiche_tags(fiche_id)")
    print(f"[long] fiche_tags : {len(long_rows)} lignes ({len(present)} axes)")


def main() -> int:
    if not CARTE_FICHES_PARQUET.exists():
        print(
            f"[error] {CARTE_FICHES_PARQUET} absent — lancer construire_fiches.py d'abord",
            file=sys.stderr,
        )
        return 1

    import duckdb

    con = duckdb.connect(":memory:")

    has_tags = FICHES_TAGS_PARQUET.exists()
    if has_tags:
        # LEFT JOIN tags on fiche_id — adds tag columns to fiches
        rows = con.execute(
            f"""
            SELECT f.* EXCLUDE(regions), t.* EXCLUDE(fiche_id)
            FROM '{CARTE_FICHES_PARQUET}' f
            LEFT JOIN '{FICHES_TAGS_PARQUET}' t USING (fiche_id)
            """
        ).fetchall()
        print(f"[tags] LEFT JOIN avec {FICHES_TAGS_PARQUET.name}")
    else:
        rows = con.execute(
            f"SELECT * EXCLUDE(regions) FROM '{CARTE_FICHES_PARQUET}'"
        ).fetchall()
        print("[tags] fiches-tags.parquet absent — sqlite sans tags")

    columns = [desc[0] for desc in con.description]
    con.close()
    print(f"[read] {len(rows)} lignes, {len(columns)} colonnes depuis {CARTE_FICHES_PARQUET.name}")

    # Écrire dans un fichier temporaire puis remplacer (atomic)
    tmp_path = CARTE_FICHES_SQLITE.with_suffix(".sqlite.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    scon = sqlite3.connect(str(tmp_path))
    col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
    scon.execute(f"CREATE TABLE fiches ({col_defs})")

    placeholders = ", ".join(["?"] * len(columns))
    scon.executemany(f"INSERT INTO fiches VALUES ({placeholders})", rows)

    for col in INDEX_COLUMNS:
        if col in columns:
            scon.execute(f'CREATE INDEX "idx_{col}" ON fiches("{col}")')
    print(f"[index] {len(INDEX_COLUMNS)} indexes créés")

    # Table longue des axes multi-label (domains, mechanisms, modifiers) :
    # 1 ligne par (fiche, axe, code). Évite le parsing JSON côté requête —
    # un GROUP BY / JOIN direct suffit pour compter ou filtrer par code.
    _build_long_tag_table(scon, columns)

    scon.commit()
    scon.execute("VACUUM")
    scon.close()

    # Atomic replace
    os.replace(tmp_path, CARTE_FICHES_SQLITE)

    size_mb = CARTE_FICHES_SQLITE.stat().st_size / 1024 / 1024
    print(f"[write] {CARTE_FICHES_SQLITE.name} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
