#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""
build_validation_sample.py — Échantillon stratifié pour la validation Phase 7.

Sélectionne ~100 fiches de constat pour la revue humaine à l'aveugle. La
stratification sur-représente délibérément les cas durs — les codes de
gravité rares (G4/G5/G6) et les fiches de confiance medium/low — pour que
la validation teste la fiabilité là où elle est en jeu, pas seulement la
majorité facile (G1/R13/R14).

Produit ``validation/validation-sample.json`` (auto-suffisant, consommé par
``validation/index.html``) : par fiche, le texte (constats + prescription)
et, dans un bloc ``machine`` séparé, les tags produits par le pipeline —
que l'interface cache jusqu'à ce que le relecteur ait soumis son propre
jugement.

Déterministe (seed fixe) : deux exécutions produisent le même échantillon.

Usage :
  uv run scripts/build_validation_sample.py            # 100 fiches
  uv run scripts/build_validation_sample.py --n 60
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import CARTE_DATA_DIR, PROJECT_ROOT  # noqa: E402

SEED = 42
FICHES = CARTE_DATA_DIR / "fiches.parquet"
TAGS = CARTE_DATA_DIR / "fiches-tags.parquet"
CODEBOOK = PROJECT_ROOT / "outputs-fiches" / "codebook.json"
OUT = PROJECT_ROOT / "validation" / "validation-sample.json"

# Quotas par bande de gravité (les rares sont sur-échantillonnées).
GRAVITY_QUOTA = {"G1": 18, "G2": 18, "G3": 18, "G4": 18, "G5": 10, "G6": 10}
DYN_BOOST = 8  # fiches supplémentaires forçant les dynamiques rares
RARE_DYN = ("R03", "R04", "R09", "R10", "R11")

AXIS_FIELD_TO_ID = {
    "domains": "axe1", "mechanisms": "axe2", "dynamic": "axe3",
    "actor": "axe4a", "stage": "axe4b", "gravity": "axe5", "trajectory": "axe6",
}


def load_axes() -> tuple[dict, dict, dict, dict]:
    """(codes par champ, code→libellé, code→définition, champ→définition d'axe)."""
    cb = json.loads(CODEBOOK.read_text(encoding="utf-8"))
    by_id = {ax["id"]: ax for ax in cb["axes"]}
    options: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    code_help: dict[str, str] = {}
    axis_help: dict[str, str] = {}
    for field, axis_id in AXIS_FIELD_TO_ID.items():
        axis = by_id[axis_id]
        options[field] = [c["id"].split("_")[0] for c in axis["codes"]]
        axis_help[field] = axis.get("description", "")
        for c in axis["codes"]:
            short = c["id"].split("_")[0]
            labels[short] = c["name"]
            code_help[short] = c.get("description", "")
    axis_help["modifiers"] = "Nuances qui accompagnent un mécanisme, jamais seules."
    options["modifiers"] = [m["id"] for m in by_id["axe2"].get("modifiers", [])]
    for m in by_id["axe2"].get("modifiers", []):
        labels[m["id"]] = m["name"]
        code_help[m["id"]] = m.get("description", "")
    return options, labels, code_help, axis_help


def confidence_rank(conf: str) -> int:
    """low/medium d'abord — ce sont les cas que l'auto-évaluation a flagués."""
    return {"low": 0, "medium": 1, "high": 2}.get(conf, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Échantillon stratifié de validation.")
    parser.add_argument("--n", type=int, default=100, help="taille cible (défaut 100)")
    args = parser.parse_args()

    import duckdb

    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT f.fiche_id, f.nom_complet, f.nom_commune, f.date_inspection,
               f.theme, f.constats_body, f.prescription,
               t.domains, t.mechanisms, t.modifiers, t.dynamic, t.actor,
               t.stage, t.gravity, t.trajectory, t.confidence
        FROM '{FICHES}' f JOIN '{TAGS}' t USING (fiche_id)
        WHERE f.constats_body IS NOT NULL AND length(f.constats_body) > 10
    """).fetchall()
    cols = [d[0] for d in con.description]
    records = [dict(zip(cols, r)) for r in rows]

    rng = random.Random(SEED)
    chosen: dict[str, dict] = {}

    def pick_from(pool: list[dict], k: int) -> None:
        # priorité : confiance faible d'abord, ordre stable ensuite
        pool = sorted(pool, key=lambda r: (confidence_rank(r["confidence"]), r["fiche_id"]))
        # mélange dans chaque tranche de confiance en gardant les low/medium devant
        for r in pool:
            if len([x for x in chosen if x]) >= args.n:
                break
            if r["fiche_id"] not in chosen and k > 0:
                chosen[r["fiche_id"]] = r
                k -= 1

    # 1. quotas par gravité
    for grav, quota in GRAVITY_QUOTA.items():
        pool = [r for r in records if r["gravity"] == grav]
        rng.shuffle(pool)
        pick_from(pool, quota)

    # 2. boost des dynamiques rares
    rare_pool = [r for r in records if r["dynamic"] in RARE_DYN and r["fiche_id"] not in chosen]
    rng.shuffle(rare_pool)
    pick_from(rare_pool, DYN_BOOST)

    # 3. complément aléatoire si sous la cible
    if len(chosen) < args.n:
        rest = [r for r in records if r["fiche_id"] not in chosen]
        rng.shuffle(rest)
        pick_from(rest, args.n - len(chosen))

    options, labels, code_help, axis_help = load_axes()
    items = []
    for r in sorted(chosen.values(), key=lambda r: r["fiche_id"]):
        items.append({
            "fiche_id": r["fiche_id"],
            "nom": r["nom_complet"], "commune": r["nom_commune"],
            "date": r["date_inspection"], "theme": r["theme"],
            "constats_body": r["constats_body"], "prescription": r["prescription"] or "",
            "machine": {
                "domains": json.loads(r["domains"] or "[]"),
                "mechanisms": json.loads(r["mechanisms"] or "[]"),
                "modifiers": json.loads(r["modifiers"] or "[]"),
                "dynamic": r["dynamic"], "actor": r["actor"], "stage": r["stage"],
                "gravity": r["gravity"], "trajectory": r["trajectory"],
                "confidence": r["confidence"],
            },
        })

    # bilan de stratification (pour la méthodo)
    from collections import Counter
    strata = {
        "gravity": dict(sorted(Counter(i["machine"]["gravity"] for i in items).items())),
        "confidence": dict(Counter(i["machine"]["confidence"] for i in items)),
        "rare_dynamique": sum(1 for i in items if i["machine"]["dynamic"] in RARE_DYN),
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "n": len(items),
        "seed": SEED,
        "source": "fiches.parquet + fiches-tags.parquet",
        "single_axes": ["dynamic", "actor", "stage", "gravity", "trajectory"],
        "multi_axes": ["domains", "mechanisms", "modifiers"],
        "options": options,
        "labels": labels,
        "code_help": code_help,
        "axis_help": axis_help,
        "strata": strata,
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[write] {OUT.relative_to(PROJECT_ROOT)} — {len(items)} fiches")
    print(f"[strata] gravity={strata['gravity']}")
    print(f"[strata] confidence={strata['confidence']}  rare-dyn={strata['rare_dynamique']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
