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
from _paths import CARTE_DATA_DIR, CARTE_METADATA_CSV, PROJECT_ROOT  # noqa: E402
from _metadonnees_util import merge_metadata  # noqa: E402
from _tags_util import canonical_code, canonical_list  # noqa: E402

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

        # Canonicalise on write : la même valeur peut arriver en forme
        # courte (R14) ou longue (R14_NEUTRE_NC) selon les batches. Le pivot
        # publié doit être canonique, sinon un SELECT DISTINCT en aval scinde
        # le code en deux (voir docs/bug-history BUG-008).
        rows.append(
            {
                "fiche_id": fiche_id,
                "domains": json.dumps(canonical_list(tag.get("domains")), ensure_ascii=False),
                "mechanisms": json.dumps(canonical_list(tag.get("mechanisms")), ensure_ascii=False),
                "modifiers": json.dumps(canonical_list(tag.get("modifiers")), ensure_ascii=False),
                "dynamic": canonical_code(tag.get("dynamic", "")) if tag.get("dynamic") else "",
                "actor": canonical_code(tag.get("actor", "")) if tag.get("actor") else "",
                "stage": canonical_code(tag.get("stage", "")) if tag.get("stage") else "",
                "gravity": canonical_code(tag.get("gravity", "")) if tag.get("gravity") else "",
                "trajectory": canonical_code(tag.get("trajectory", "")) if tag.get("trajectory") else "",
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

    # Self-check : la sortie doit être canonique (invariant du pivot publié).
    for r in rows:
        singles = (r["dynamic"], r["actor"], r["stage"], r["gravity"], r["trajectory"])
        if any(v and canonical_code(v) != v for v in singles):
            print(f"[error] code non canonique après normalisation : {r['fiche_id']}", file=sys.stderr)
            return 1

    # Write parquet via DuckDB. Toutes les colonnes sont des chaînes
    # (valeur simple ou JSON array string) — table à schéma explicite +
    # insert par lot, DuckDB ne sait pas scanner une liste de dicts.
    import duckdb

    columns = [
        "fiche_id", "domains", "mechanisms", "modifiers", "dynamic",
        "actor", "stage", "gravity", "trajectory", "confidence",
    ]
    con = duckdb.connect(":memory:")
    con.execute(
        f"CREATE TABLE tags ({', '.join(f'{c} VARCHAR' for c in columns)})"
    )
    placeholders = ", ".join("?" for _ in columns)
    con.executemany(
        f"INSERT INTO tags VALUES ({placeholders})",
        [[r[c] for c in columns] for r in rows],
    )
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

    _write_metadata()
    return 0


def _write_metadata() -> None:
    """Documente les colonnes de tags dans le dictionnaire partagé, pour
    que le catalogue /donnees/ expose le sens des codes (un lecteur peut
    ainsi résoudre R09 → blocage tiers sans ouvrir la taxonomie)."""
    owner = OUTPUT_PARQUET.name  # fiches-tags.parquet
    defs = [
        ("fiche_id", "Clé de jointure vers fiches.parquet (une fiche de constat)."),
        ("domains", "Axe 1 — domaine(s) technique(s), multi-label JSON. "
         "D01 incendie, D02 ATEX, D03 Seveso, D04 eaux, D05 air/bruit, "
         "D06 sols, D07 rétention, D08 déchets, D09 biodiversité, "
         "D10 électrique/ESP, D11 sécurité, D12 admin, D13 cessation, "
         "D14 risque bio, D15 secteur, D16 autre."),
        ("mechanisms", "Axe 2 — mécanisme(s) réglementaire(s), multi-label JSON. "
         "M01 documentation, M04 mise en demeure, M07 contrôle, "
         "M08 conformité, M14 constat technique, M17 classification, "
         "M19 cessation (liste complète : taxonomy-v5)."),
        ("modifiers", "Modificateurs, multi-label JSON. m_DELAI délai correctif "
         "chiffré imposé ; m_MENACE avertissement conditionnel (« à défaut, MED »)."),
        ("dynamic", "Axe 3 — dynamique relationnelle exploitant/inspection. "
         "R01 proactif, R02 promesse, R03 façade, R04 méconnaissance, "
         "R09 blocage tiers, R10 tension, R13 neutre conforme, R14 neutre NC "
         "(liste complète : taxonomy-v5)."),
        ("actor", "Axe 4a — acteur principal (A01 exploitant, A02 inspection, "
         "A03 préfet, A04 SDIS…)."),
        ("stage", "Axe 4b — stade procédural (S01 constat, S02 injonction, "
         "S03 correction, S04 vérification, S05 escalade…)."),
        ("gravity", "Axe 5 — gravité du constat, G1 (observation) à G6 (le plus grave). "
         "Absente des données brutes Géorisques."),
        ("trajectory", "Axe 6 — trajectoire temporelle. T1 premier constat, "
         "T2 suivi, T3 amélioration, T4 stagnation, T7 chronique…"),
        ("confidence", "Confiance du tagging (high/medium/low), issue de la passe Skeptic."),
    ]
    owner_rows = [
        {"fichier": owner, "nom_original": alias, "alias": alias, "definition": definition}
        for alias, definition in defs
    ]
    merge_metadata(CARTE_METADATA_CSV, owner, owner_rows)
    print(f"[meta] {len(owner_rows)} colonnes de tags documentées dans {CARTE_METADATA_CSV.name}")


if __name__ == "__main__":
    sys.exit(main())
