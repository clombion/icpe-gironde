#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
# ]
# ///
"""
merge_tags.py — Fusionne les tags Hunter/Skeptic en fiches-tags.parquet.

Lit tous les fichiers outputs-fiches/tags/final-*.json (sortie Skeptic corrigée),
joint au manifest pour récupérer le fiche_id, et écrit carte/data/fiches-tags.parquet.

Par défaut, un batch sans final-*.json (audit Skeptic absent) fait échouer
le merge ; --allow-hunter-fallback accepte la sortie Hunter brute à la place.

Colonnes produites :
  fiche_id, domains, mechanisms, modifiers, dynamic, actor, stage,
  gravity, trajectory, confidence

Les colonnes multi-label (domains, mechanisms, modifiers) sont stockées
en JSON array string (e.g. '["D01","D02"]'). Les colonnes single-value
sont des strings simples (e.g. "G3").

Usage :
  uv run scripts/merge_tags.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import CARTE_DATA_DIR, PROJECT_ROOT  # noqa: E402

TAGS_DIR = PROJECT_ROOT / "outputs-fiches" / "tags"
MANIFEST_PATH = PROJECT_ROOT / "outputs-fiches" / "manifest-all.csv"
OUTPUT_PARQUET = CARTE_DATA_DIR / "fiches-tags.parquet"


def load_manifest() -> dict[str, str]:
    """Return slug→fiche_id mapping from manifest-all.csv."""
    mapping: dict[str, str] = {}
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mapping[row["slug"]] = row["fiche_id"]
    return mapping


def load_tags(allow_hunter_fallback: bool) -> list[dict]:
    """Load all tag files, preferring final-*.json over hunter-*.json.

    Sans --allow-hunter-fallback, tout batch dont le final (audit Skeptic)
    manque fait échouer le merge : le pivot publié ne doit pas mélanger
    silencieusement des tags audités et non audités.
    """
    if not TAGS_DIR.exists():
        print(f"[error] {TAGS_DIR} absent", file=sys.stderr)
        sys.exit(1)

    # Collect final files first, fall back to hunter
    final_files = sorted(TAGS_DIR.glob("final-*.json"))
    hunter_files = sorted(TAGS_DIR.glob("hunter-*.json"))

    if not final_files and not hunter_files:
        print("[error] Aucun fichier tags trouvé", file=sys.stderr)
        sys.exit(1)

    # Determine which batches have final vs only hunter
    final_batches = {f.stem.replace("final-", "") for f in final_files}
    unaudited = sorted(
        hf.stem.replace("hunter-", "")
        for hf in hunter_files
        if hf.stem.replace("hunter-", "") not in final_batches
    )
    if unaudited and not allow_hunter_fallback:
        print(
            f"[error] {len(unaudited)} batch(es) sans final Skeptic : "
            f"{', '.join(unaudited)}",
            file=sys.stderr,
        )
        print(
            "[error] Terminer l'audit Skeptic (voir scripts/tag_status.py) ou "
            "relancer avec --allow-hunter-fallback pour merger quand même.",
            file=sys.stderr,
        )
        sys.exit(1)

    files_to_load: list[Path] = list(final_files)
    for hf in hunter_files:
        batch = hf.stem.replace("hunter-", "")
        if batch not in final_batches:
            files_to_load.append(hf)
            print(f"[warn] batch {batch}: using hunter (no final yet)")

    all_tags: list[dict] = []
    for fp in sorted(files_to_load):
        with fp.open(encoding="utf-8") as f:
            data = json.load(f)
        # Support both list-of-records and dict with records key
        records = data if isinstance(data, list) else data.get("records", data.get("fiches", []))
        all_tags.extend(records)
        print(f"[read] {fp.name}: {len(records)} fiches")

    return all_tags


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fusionne les tags Hunter/Skeptic en fiches-tags.parquet."
    )
    parser.add_argument(
        "--allow-hunter-fallback",
        action="store_true",
        help=(
            "accepte les batches sans audit Skeptic (final-*.json manquant) "
            "en utilisant la sortie Hunter brute — par défaut le merge refuse"
        ),
    )
    args = parser.parse_args()

    slug_to_fiche = load_manifest()
    print(f"[manifest] {len(slug_to_fiche)} slugs chargés")

    all_tags = load_tags(allow_hunter_fallback=args.allow_hunter_fallback)
    print(f"[tags] {len(all_tags)} fiches taggées au total")

    # Build rows for parquet
    rows: list[dict] = []
    missing_slugs: list[str] = []

    for tag in all_tags:
        slug = tag.get("slug", "")
        fiche_id = slug_to_fiche.get(slug)
        if not fiche_id:
            missing_slugs.append(slug)
            continue

        rows.append(
            {
                "fiche_id": fiche_id,
                "domains": json.dumps(tag.get("domains", []), ensure_ascii=False),
                "mechanisms": json.dumps(tag.get("mechanisms", []), ensure_ascii=False),
                "modifiers": json.dumps(tag.get("modifiers", []), ensure_ascii=False),
                "dynamic": tag.get("dynamic", ""),
                "actor": tag.get("actor", ""),
                "stage": tag.get("stage", ""),
                "gravity": tag.get("gravity", ""),
                "trajectory": tag.get("trajectory", ""),
                "confidence": tag.get("confidence", ""),
            }
        )

    if missing_slugs:
        print(f"[warn] {len(missing_slugs)} slugs non trouvés dans le manifest")
        for s in missing_slugs[:5]:
            print(f"  - {s}")

    if not rows:
        print("[error] Aucune ligne à écrire", file=sys.stderr)
        return 1

    # Write parquet via DuckDB
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE tags AS SELECT * FROM rows")
    con.execute(f"COPY tags TO '{OUTPUT_PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()

    size_kb = OUTPUT_PARQUET.stat().st_size / 1024
    print(f"[write] {OUTPUT_PARQUET.name} ({len(rows)} lignes, {size_kb:.0f} KB)")

    # Summary stats
    from collections import Counter

    gravity_dist = Counter(r["gravity"] for r in rows)
    confidence_dist = Counter(r["confidence"] for r in rows)
    print(f"[stats] gravity: {dict(sorted(gravity_dist.items()))}")
    print(f"[stats] confidence: {dict(sorted(confidence_dist.items()))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
