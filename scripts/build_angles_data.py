#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
# ]
# ///
"""
build_angles_data.py — Pré-calcule les résultats SQL des angles d'analyse.

Chaque fichier ``.md`` dans ``rapports/angles/`` contient un bloc SQL
fenced (````sql ... ````). Ce script lit le SQL, l'exécute contre
``fiches.parquet`` via DuckDB, et écrit le résultat en JSON à côté du
``.md`` (même basename, extension ``.json``).

Le frontend ``angles.js`` charge ces JSON pré-calculés — aucun moteur
SQL n'est nécessaire côté browser pour la page des angles.

Les requêtes SQL dans les ``.md`` utilisent ``FROM 'fiches.parquet'``.
Le script crée une vue DuckDB ``fiches`` mappée sur le parquet pour
que les deux syntaxes fonctionnent.

Usage :
  uv run scripts/build_angles_data.py

Dépendances : duckdb (PEP 723).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import CARTE_FICHES_PARQUET, PROJECT_ROOT  # noqa: E402

ANGLES_DIR = PROJECT_ROOT / "rapports" / "angles"

_SQL_BLOCK_RE = re.compile(r"```sql\n([\s\S]+?)```")


def extract_sql(md_text: str) -> str | None:
    """Extrait le premier bloc SQL fenced d'un fichier markdown."""
    match = _SQL_BLOCK_RE.search(md_text)
    return match.group(1).strip() if match else None


def main() -> int:
    if not CARTE_FICHES_PARQUET.exists():
        print(
            f"[error] {CARTE_FICHES_PARQUET} absent — lancer construire_fiches.py d'abord",
            file=sys.stderr,
        )
        return 1

    if not ANGLES_DIR.exists():
        print(f"[error] {ANGLES_DIR} absent", file=sys.stderr)
        return 1

    import duckdb

    con = duckdb.connect(":memory:")
    # Vue pour que FROM 'fiches.parquet' et FROM fiches fonctionnent
    con.execute(f"CREATE VIEW fiches AS SELECT * FROM '{CARTE_FICHES_PARQUET}'")

    md_files = sorted(ANGLES_DIR.glob("*.md"))
    if not md_files:
        print("[warn] aucun fichier .md dans rapports/angles/")
        return 0

    errors = 0
    for md_path in md_files:
        md_text = md_path.read_text(encoding="utf-8")
        sql = extract_sql(md_text)
        if not sql:
            continue

        # Réécrire FROM 'fiches.parquet' → FROM fiches (la vue)
        sql_exec = sql.replace("'fiches.parquet'", "fiches")

        json_path = md_path.with_suffix(".json")
        try:
            result = con.execute(sql_exec).fetchall()
            columns = [desc[0] for desc in con.description]
            rows = [dict(zip(columns, row)) for row in result]

            # Atomic write
            tmp_path = json_path.with_suffix(".json.tmp")
            content = json.dumps(rows, ensure_ascii=False, default=str)
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, json_path)

            size_kb = json_path.stat().st_size / 1024
            print(f"  {md_path.name}: {len(rows)} lignes, {size_kb:.0f} KB")
        except Exception as exc:
            print(f"  ERROR {md_path.name}: {exc}", file=sys.stderr)
            errors += 1

    con.close()

    if errors:
        print(f"\n[error] {errors} angle(s) en erreur", file=sys.stderr)
        return 2

    print(f"\n[build] {len(md_files)} angles traités → {ANGLES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
