#!/usr/bin/env python3
"""
fix_tag_aliases.py — Corrections mécaniques des alias dans les tags final-*.json.

Applique uniquement les deux corrections prouvées non ambiguës :
  1. Déplacement d'axe : m_DELAI / m_MENACE trouvés dans `mechanisms`
     rejoignent `modifiers` (dédupliqué).
  2. Alias de suffixe : M04_MED → M04_MISE_EN_DEMEURE dans `mechanisms`.

Les codes sémantiquement ambigus (D09_PAC, R13/R14, …) ne sont PAS touchés —
ils passent par ré-adjudication (lecture de la fiche), pas par une règle.

Ne touche que les final-*.json (les hunter des batches non audités sont
corrigés par la passe Skeptic au prompt v1.1). Chaque application est
enregistrée dans outputs-fiches/tags/_provenance.jsonl.

Par défaut : dry-run (liste les corrections sans écrire). --apply pour écrire.

Codes de sortie : 0 OK (y compris rien à faire), 1 erreur.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import PROJECT_ROOT  # noqa: E402

TAGS_DIR = PROJECT_ROOT / "outputs-fiches" / "tags"
PROVENANCE = TAGS_DIR / "_provenance.jsonl"

AXIS_MOVES = {"m_DELAI", "m_MENACE"}  # mechanisms → modifiers
SUFFIX_ALIASES = {"M04_MED": "M04_MISE_EN_DEMEURE"}


def fix_entry(entry: dict) -> list[str]:
    """Corrige une entrée en place ; retourne la description des corrections."""
    changes: list[str] = []
    mechanisms = entry.get("mechanisms") or []
    modifiers = entry.get("modifiers") or []

    kept: list[str] = []
    for code in mechanisms:
        if code in AXIS_MOVES:
            if code not in modifiers:
                modifiers.append(code)
            changes.append(f"{code}: mechanisms → modifiers")
        elif code in SUFFIX_ALIASES:
            kept.append(SUFFIX_ALIASES[code])
            changes.append(f"{code} → {SUFFIX_ALIASES[code]}")
        else:
            kept.append(code)
    if changes:
        entry["mechanisms"] = kept
        entry["modifiers"] = modifiers
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Corrections mécaniques des alias dans les tags final-*.json."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="écrit les corrections (défaut : dry-run, affiche sans écrire)",
    )
    args = parser.parse_args()

    total = 0
    for path in sorted(TAGS_DIR.glob("final-*.json")):
        entries = json.loads(path.read_text(encoding="utf-8"))
        file_changes: list[tuple[str, list[str]]] = []
        for entry in entries:
            changes = fix_entry(entry)
            if changes:
                file_changes.append((entry.get("slug", "?"), changes))
        if not file_changes:
            continue
        total += sum(len(c) for _, c in file_changes)
        print(f"{path.name}: {sum(len(c) for _, c in file_changes)} correction(s)")
        for slug, changes in file_changes:
            for change in changes:
                print(f"  {slug[:60]}: {change}")
        if args.apply:
            path.write_text(
                json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
            with PROVENANCE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "step": "fix_tag_aliases",
                    "file": path.name,
                    "corrections": sum(len(c) for _, c in file_changes),
                    "rules": "axis-move m_* mechanisms→modifiers; M04_MED→M04_MISE_EN_DEMEURE",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")

    mode = "appliquées" if args.apply else "planifiées (dry-run — relancer avec --apply)"
    print(f"\n{total} correction(s) {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
