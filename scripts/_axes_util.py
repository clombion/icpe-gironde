"""
_axes_util.py — Chargement des axes de la taxonomie depuis le codebook.

Source partagée pour tout script qui a besoin de la correspondance
code → libellé / définition des axes v5. Réutilisé par
``build_validation_sample.py`` (échantillon de validation) et
``build_taxonomy_labels.py`` (projection pour le site web).

Convention des codes courts : les axes 1-6 utilisent le préfixe de l'id
(``D01_INCENDIE`` → ``D01``) ; les modificateurs gardent leur id complet
(``m_DELAI``), car c'est sous cette forme qu'ils sont stockés dans la base.

Pas de side-effect à l'import — purement déclaratif. Stdlib uniquement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import PROJECT_ROOT  # noqa: E402

CODEBOOK = PROJECT_ROOT / "outputs-fiches" / "codebook.json"

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
