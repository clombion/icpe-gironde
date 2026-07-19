"""
build_taxonomy_labels.py — Génère carte/data/taxonomy-labels.json.

Projection navigateur de la taxonomie v5 : libellés et définitions par axe,
consommée par l'explorateur par tags (/rapports/) et, plus tard, les angles
curatés. Le codebook (`outputs-fiches/codebook.json`, via `load_axes`) est la
source des libellés ; la base (`carte/data/fiches.sqlite`) est l'autorité du
genre d'axe (mono/multi-label) et le garde-fou de couverture des codes.

Deux garde-fous au build (échouent la génération, jamais silencieux) :

- **Couverture des codes** : tout code réellement présent en base
  (``fiche_tags.code`` ∪ valeurs non-nulles des colonnes mono-label) doit
  avoir un libellé. Sinon un filtre web renverrait 0 ligne en silence
  (classe BUG-008 : le code court navigateur et le code base ne coïncident
  que par convention — cette assertion les lie).
- **Invariant co-NULL** : les six axes mono-label sont NULL ensemble (fiche
  taggée = tous les axes renseignés). C'est ce qui rend ``gravity IS NOT
  NULL`` un proxy exact du corpus taggé côté explorateur.

Stdlib uniquement (sqlite3), pas de duckdb.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _axes_util import load_axes  # noqa: E402
from _paths import CARTE_DATA_DIR  # noqa: E402

SQLITE = CARTE_DATA_DIR / "fiches.sqlite"
OUT = CARTE_DATA_DIR / "taxonomy-labels.json"

# Ordre d'affichage des axes (primaires d'abord, puis « Plus de filtres »).
AXIS_ORDER = ["gravity", "domains", "mechanisms", "trajectory",
              "stage", "actor", "dynamic", "modifiers", "confidence"]
AXIS_NAME = {
    "gravity": "Gravité", "domains": "Domaine technique",
    "mechanisms": "Mécanisme", "trajectory": "Trajectoire",
    "stage": "Stade", "actor": "Acteur", "dynamic": "Dynamique",
    "modifiers": "Modificateurs", "confidence": "Confiance",
}
# Axes mono-label stockés en colonnes scalaires sur `fiches`.
SINGLE_LABEL_COLUMNS = ["gravity", "dynamic", "trajectory", "stage", "actor", "confidence"]
# `confidence` n'est pas dans le codebook : libellés synthétisés.
CONFIDENCE = [("high", "Élevée"), ("medium", "Moyenne"), ("low", "Faible")]


def db_multi_label_axes(con: sqlite3.Connection) -> set[str]:
    """Autorité du genre d'axe : les axes présents dans fiche_tags sont multi-label."""
    return {row[0] for row in con.execute("SELECT DISTINCT axis FROM fiche_tags")}


def db_codes_in_use(con: sqlite3.Connection) -> set[str]:
    """Tous les codes réellement stockés : fiche_tags + colonnes mono-label."""
    codes = {row[0] for row in con.execute("SELECT DISTINCT code FROM fiche_tags")}
    for col in SINGLE_LABEL_COLUMNS:
        codes |= {
            row[0]
            for row in con.execute(
                f"SELECT DISTINCT {col} FROM fiches WHERE {col} IS NOT NULL AND {col} != ''"
            )
        }
    return codes


def assert_co_null_invariant(con: sqlite3.Connection) -> None:
    """M4 : les six axes mono-label sont NULL ensemble (0 fiche partiellement taggée)."""
    checks = " OR ".join(
        f"(gravity IS NULL) != ({col} IS NULL)" for col in SINGLE_LABEL_COLUMNS if col != "gravity"
    )
    (n,) = con.execute(f"SELECT COUNT(*) FROM fiches WHERE {checks}").fetchone()
    if n:
        raise SystemExit(
            f"[co-NULL] {n} fiche(s) partiellement taggée(s) — "
            "`gravity IS NOT NULL` n'est plus un proxy exact du corpus taggé. "
            "Corriger le pipeline de merge avant de régénérer."
        )


def main() -> int:
    options, labels, code_help, _ = load_axes()
    labels = dict(labels)
    code_help = dict(code_help)
    for code, lab in CONFIDENCE:
        labels[code] = lab
        code_help.setdefault(code, "")
    options = dict(options)
    options["confidence"] = [c for c, _ in CONFIDENCE]

    con = sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True)
    try:
        multi = db_multi_label_axes(con)
        in_use = db_codes_in_use(con)
        assert_co_null_invariant(con)
    finally:
        con.close()

    # M3 : couverture des codes — tout code en base doit avoir un libellé.
    missing = sorted(c for c in in_use if c not in labels)
    if missing:
        raise SystemExit(
            f"[couverture] {len(missing)} code(s) en base sans libellé : {missing}. "
            "Le codebook et la base ont divergé (classe BUG-008)."
        )

    axes = []
    for field in AXIS_ORDER:
        codes = [
            {"code": c, "label": labels.get(c, c), "help": code_help.get(c, "")}
            for c in options.get(field, [])
        ]
        axes.append({
            "field": field,
            "name": AXIS_NAME[field],
            "multi_label": field in multi,  # autorité = la base (D12)
            "codes": codes,
        })

    out = {
        "version": "v5",
        "axes": axes,
        "labels": labels,
        "axis_name": AXIS_NAME,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n_codes = sum(len(a["codes"]) for a in axes)
    print(f"écrit {OUT} — {n_codes} codes, axes multi-label={sorted(multi)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
