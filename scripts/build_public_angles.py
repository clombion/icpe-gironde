"""
build_public_angles.py — Pré-calcule les figures des angles publics (/enquete/).

Trois constats grand public tirés du classement automatique (taxonomie v5) :
routine, risques avérés, récidivistes. Sortie : ``enquete/angles.json``,
consommé par une page statique (pas de sql.js, pas de 78 Mo).

Source : ``carte/data/fiches.sqlite`` — colonnes scalaires sur ``fiches``
(gravity, trajectory) et la table longue explosée ``fiche_tags(fiche_id,
axis, code)`` pour les axes multi-label (domains, mechanisms). Les colonnes
``mechanisms``/``domains`` de ``fiches`` sont du JSON-dans-VARCHAR :
``WHERE mechanisms = 'M08'`` renverrait 0 (classe BUG-008) — on passe donc
toujours par ``fiche_tags``.

Garde-fou : chaque figure est réconciliée à un **oracle codé en dur et
indépendant** (pas un re-calcul de la même expression), et le build échoue
sur tout écart. Stdlib uniquement.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import CARTE_DATA_DIR, PROJECT_ROOT  # noqa: E402

SQLITE = CARTE_DATA_DIR / "fiches.sqlite"
LABELS = CARTE_DATA_DIR / "taxonomy-labels.json"
OUT = PROJECT_ROOT / "enquete" / "angles.json"

# Oracles indépendants (vérifiés à la main contre la base). Le build échoue
# si une figure calculée ne les reproduit pas.
ORACLES = {
    "tagged": 10514, "untagged": 478,
    "routine_g1g2": 7096, "conformite_m08": 4539,
    "tail_g4g6": 743, "tail_D01": 202, "tail_D04": 161, "tail_D08": 117,
    "traj_t5": 114, "traj_t7": 110, "mise_en_demeure_m04": 772,
}


def check(name: str, got: int) -> int:
    """Réconcilie une figure à son oracle ; échoue le build sur écart."""
    want = ORACLES[name]
    if got != want:
        raise SystemExit(f"[oracle] {name} = {got}, attendu {want} — la base a bougé, revoir les figures.")
    return got


def scalar(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return con.execute(sql, params).fetchone()[0]


def tag_count(con: sqlite3.Connection, axis: str, code: str) -> int:
    """Nombre de fiches taggées portant (axis, code) — via la table explosée."""
    return scalar(
        con,
        "SELECT COUNT(DISTINCT t.fiche_id) FROM fiche_tags t JOIN fiches f ON f.fiche_id = t.fiche_id "
        "WHERE t.axis = ? AND t.code = ? AND f.gravity IS NOT NULL",
        (axis, code),
    )


def main() -> int:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]
    con = sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True)
    try:
        tagged = check("tagged", scalar(con, "SELECT COUNT(*) FROM fiches WHERE gravity IS NOT NULL"))
        untagged = check("untagged", scalar(con, "SELECT COUNT(*) FROM fiches WHERE gravity IS NULL"))

        # --- Angle 1 : la routine ---
        g1g2 = check("routine_g1g2", scalar(con, "SELECT COUNT(*) FROM fiches WHERE gravity IN ('G1','G2')"))
        m08 = check("conformite_m08", tag_count(con, "mechanisms", "M08"))

        # --- Angle 2 : risques avérés (queue G4-G6, par domaine) ---
        tail = check("tail_g4g6", scalar(con, "SELECT COUNT(*) FROM fiches WHERE gravity IN ('G4','G5','G6')"))
        tail_by_domain = con.execute(
            "SELECT t.code, COUNT(DISTINCT f.fiche_id) n FROM fiches f "
            "JOIN fiche_tags t ON t.fiche_id = f.fiche_id AND t.axis = 'domains' "
            "WHERE f.gravity IN ('G4','G5','G6') GROUP BY t.code ORDER BY n DESC LIMIT 6"
        ).fetchall()
        for code in ("D01", "D04", "D08"):  # incendie / eaux / déchets
            check(f"tail_{code}", next(n for c, n in tail_by_domain if c == code))

        # --- Angle 3 : récidivistes (T5/T7 + M04) + classement nommé ---
        t5 = check("traj_t5", scalar(con, "SELECT COUNT(*) FROM fiches WHERE trajectory = 'T5'"))
        t7 = check("traj_t7", scalar(con, "SELECT COUNT(*) FROM fiches WHERE trajectory = 'T7'"))
        m04 = check("mise_en_demeure_m04", tag_count(con, "mechanisms", "M04"))
        named = con.execute(
            "SELECT nom_complet, nom_commune, COUNT(*) n FROM fiches "
            "WHERE trajectory IN ('T5','T7') AND nom_complet IS NOT NULL AND nom_complet != '' "
            "GROUP BY nom_complet ORDER BY n DESC, nom_complet LIMIT 10"
        ).fetchall()
    finally:
        con.close()

    def bars(rows: list[tuple[str, int]]) -> list[dict]:
        return [{"code": c, "label": labels.get(c, c), "n": n} for c, n in rows]

    doc = {
        "corpus": {"tagged": tagged, "untagged": untagged, "total": tagged + untagged},
        "angles": [
            {
                "id": "routine",
                "title": "L'inspection, c'est surtout de la routine",
                "big": {"value": g1g2, "of": tagged, "pct": round(g1g2 / tagged * 100, 1)},
                "detail": {"m08": m08, "m08_label": labels.get("M08", "M08")},
                "creuser": "../rapports/explorer.html#mechanisms=M08",
            },
            {
                "id": "risques-averes",
                "title": "Où sont les risques avérés",
                "big": {"value": tail, "of": tagged, "pct": round(tail / tagged * 100, 1)},
                "bars": bars(tail_by_domain),
                "creuser": "../rapports/explorer.html#gravity=G4,G5,G6",
            },
            {
                "id": "recidivistes",
                "title": "Les récidivistes",
                "signals": [
                    {"key": "aggravation", "code": "T5", "label": labels.get("T5", "T5"), "n": t5},
                    {"key": "chronique", "code": "T7", "label": labels.get("T7", "T7"), "n": t7},
                    {"key": "mise_en_demeure", "code": "M04", "label": labels.get("M04", "M04"), "n": m04},
                ],
                "named": [{"nom": nom, "commune": com, "n": n} for nom, com, n in named],
                "creuser": "../rapports/explorer.html#trajectory=T5,T7",
            },
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"écrit {OUT} — 3 angles, {len(named)} récidivistes nommés, oracles OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
