#!/usr/bin/env python3
"""
tag_status.py — État et validation du tagging Pass 4 (Hunter/Skeptic).

Rapporte la couverture des batches (hunter-*.json / final-*.json), valide
chaque fichier de tags contre la liste de slugs de son batch et contre la
taxonomie du codebook, et mesure le taux de correction Skeptic
(hunter vs final) par batch et en agrégat.

Prérequis (validés au démarrage) :
  - outputs-fiches/tags/batches/batch-*-slugs.txt  (listes de slugs par batch)
  - outputs-fiches/codebook.json                    (axes + codes valides)
  - outputs-fiches/tags/hunter-*.json               (au moins un)

Codes de sortie :
  0  couverture complète et aucune erreur de validation
  1  batches manquants ou erreurs de validation détectées
  2  prérequis absents (répertoires/fichiers introuvables)

Usage :
  python3 scripts/tag_status.py            # rapport lisible
  python3 scripts/tag_status.py --json     # rapport machine (stdout)
  python3 scripts/tag_status.py --quiet    # uniquement les batches final manquants
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import PROJECT_ROOT  # noqa: E402

__version__ = "1.0.0"

OUTPUTS_DIR = PROJECT_ROOT / "outputs-fiches"
TAGS_DIR = OUTPUTS_DIR / "tags"
BATCHES_DIR = TAGS_DIR / "batches"
CODEBOOK_PATH = OUTPUTS_DIR / "codebook.json"

CONFIDENCE_VALUES = {"high", "medium", "low"}

# Champ du tag → clé de l'axe codebook. Les modificateurs vivent sous
# axes[axe2].modifiers, traités à part.
AXIS_FIELDS = {
    "domains": "axe1",
    "mechanisms": "axe2",
    "dynamic": "axe3",
    "actor": "axe4a",
    "stage": "axe4b",
    "gravity": "axe5",
    "trajectory": "axe6",
}
MULTI_LABEL_FIELDS = {"domains", "mechanisms", "modifiers"}


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_valid_codes(codebook_path: Path) -> dict[str, set[str]]:
    """Codes valides par champ. Un code court (avant « _ ») est accepté
    au même titre que l'id complet — les tags gravité/trajectoire sont
    émis en forme courte (G1) alors que le codebook les nomme en long
    (G1_OBSERVATION)."""
    codebook = json.loads(codebook_path.read_text(encoding="utf-8"))
    by_axis: dict[str, set[str]] = {}
    modifiers: set[str] = set()
    for axis in codebook["axes"]:
        codes: set[str] = set()
        for code in axis.get("codes", []):
            codes.add(code["id"])
            codes.add(code["id"].split("_")[0])
        by_axis[axis["id"]] = codes
        for mod in axis.get("modifiers", []):
            modifiers.add(mod["id"])
    valid = {field: by_axis[axis_id] for field, axis_id in AXIS_FIELDS.items()}
    valid["modifiers"] = modifiers
    return valid


def load_batch_slugs(batches_dir: Path) -> dict[str, list[str]]:
    slugs_by_batch: dict[str, list[str]] = {}
    for path in sorted(batches_dir.glob("batch-*-slugs.txt")):
        batch = path.stem.replace("batch-", "").replace("-slugs", "")
        slugs_by_batch[batch] = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    return slugs_by_batch


def load_tag_file(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"JSON illisible : {exc}"
    if not isinstance(data, list):
        return None, f"attendu une liste, trouvé {type(data).__name__}"
    return data, None


def validate_entries(
    entries: list[dict[str, Any]],
    expected_slugs: list[str],
    valid_codes: dict[str, set[str]],
) -> list[str]:
    """Erreurs de validation d'un fichier de tags (liste vide = conforme)."""
    errors: list[str] = []
    got_slugs = [e.get("slug", "") for e in entries]
    if len(entries) != len(expected_slugs):
        errors.append(f"{len(entries)} entrées, {len(expected_slugs)} slugs attendus")
    missing = set(expected_slugs) - set(got_slugs)
    extra = set(got_slugs) - set(expected_slugs)
    if missing:
        errors.append(f"{len(missing)} slugs absents (ex. {sorted(missing)[0]})")
    if extra:
        errors.append(f"{len(extra)} slugs hors batch (ex. {sorted(extra)[0]})")
    dupes = len(got_slugs) - len(set(got_slugs))
    if dupes:
        errors.append(f"{dupes} slugs dupliqués")

    bad_codes: dict[str, set[str]] = {}
    bad_confidence = 0
    for entry in entries:
        for field, codes in valid_codes.items():
            values = entry.get(field)
            if field in MULTI_LABEL_FIELDS:
                values = values if isinstance(values, list) else []
            else:
                values = [values] if values else []
            for value in values:
                if value not in codes:
                    bad_codes.setdefault(field, set()).add(str(value))
        if entry.get("confidence") not in CONFIDENCE_VALUES:
            bad_confidence += 1
    for field, values in sorted(bad_codes.items()):
        sample = ", ".join(sorted(values)[:3])
        errors.append(f"codes hors taxonomie sur {field} : {sample}")
    if bad_confidence:
        errors.append(f"{bad_confidence} entrées avec confidence invalide")
    return errors


def correction_stats(
    hunter: list[dict[str, Any]], final: list[dict[str, Any]]
) -> tuple[int, int]:
    """(fiches corrigées, fiches comparées) entre sortie Hunter et final."""
    compared_fields = list(AXIS_FIELDS) + ["modifiers"]
    final_by_slug = {e.get("slug"): e for e in final}
    corrected = 0
    compared = 0
    for h in hunter:
        f = final_by_slug.get(h.get("slug"))
        if f is None:
            continue
        compared += 1
        if any(h.get(field) != f.get(field) for field in compared_fields):
            corrected += 1
    return corrected, compared


def build_report(
    slugs_by_batch: dict[str, list[str]], valid_codes: dict[str, set[str]]
) -> dict[str, Any]:
    batches = sorted(slugs_by_batch)
    report: dict[str, Any] = {
        "expected_batches": len(batches),
        "hunter_done": [],
        "hunter_missing": [],
        "final_done": [],
        "final_missing": [],
        "validation_errors": {},
        "correction": {"per_batch": {}, "corrected": 0, "compared": 0},
        "total_fiches": sum(len(s) for s in slugs_by_batch.values()),
    }
    for batch in batches:
        expected = slugs_by_batch[batch]
        loaded: dict[str, list[dict[str, Any]]] = {}
        has_final = (TAGS_DIR / f"final-{batch}.json").exists()
        for kind in ("hunter", "final"):
            path = TAGS_DIR / f"{kind}-{batch}.json"
            if not path.exists():
                report[f"{kind}_missing"].append(batch)
                continue
            report[f"{kind}_done"].append(batch)
            entries, err = load_tag_file(path)
            if err:
                report["validation_errors"][path.name] = [err]
                continue
            loaded[kind] = entries
            # Un hunter supplanté par un final n'est plus consommé par le
            # merge — sa validité ne compte que tant que le final n'existe pas.
            if kind == "hunter" and has_final:
                continue
            errors = validate_entries(entries, expected, valid_codes)
            if errors:
                report["validation_errors"][path.name] = errors
        if "hunter" in loaded and "final" in loaded:
            corrected, compared = correction_stats(loaded["hunter"], loaded["final"])
            report["correction"]["per_batch"][batch] = {
                "corrected": corrected,
                "compared": compared,
            }
            report["correction"]["corrected"] += corrected
            report["correction"]["compared"] += compared
    return report


def print_human(report: dict[str, Any]) -> None:
    n = report["expected_batches"]
    print(f"Batches attendus       : {n} ({report['total_fiches']} fiches)")
    print(f"Hunter                 : {len(report['hunter_done'])}/{n}")
    if report["hunter_missing"]:
        print(f"  manquants : {', '.join(report['hunter_missing'])}")
    print(f"Final (Skeptic)        : {len(report['final_done'])}/{n}")
    if report["final_missing"]:
        print(f"  manquants : {', '.join(report['final_missing'])}")
    corr = report["correction"]
    if corr["compared"]:
        rate = 100 * corr["corrected"] / corr["compared"]
        print(
            f"Taux de correction     : {rate:.1f} % "
            f"({corr['corrected']}/{corr['compared']} fiches, cible < 15 %)"
        )
        worst = sorted(
            corr["per_batch"].items(),
            key=lambda kv: kv[1]["corrected"] / max(kv[1]["compared"], 1),
            reverse=True,
        )[:5]
        detail = ", ".join(
            f"{b} ({v['corrected']}/{v['compared']})" for b, v in worst if v["corrected"]
        )
        if detail:
            print(f"  batches les plus corrigés : {detail}")
    if report["validation_errors"]:
        print(f"Erreurs de validation  : {len(report['validation_errors'])} fichier(s)")
        for name, errors in sorted(report["validation_errors"].items()):
            for error in errors:
                print(f"  {name}: {error}")
    else:
        print("Erreurs de validation  : aucune")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "État et validation du tagging Pass 4 : couverture des batches, "
            "conformité à la taxonomie, taux de correction Skeptic."
        ),
        epilog=(
            "Exemples :\n"
            "  python3 scripts/tag_status.py\n"
            "  python3 scripts/tag_status.py --json | jq .final_missing\n"
            "  python3 scripts/tag_status.py --quiet   # batches final à traiter\n\n"
            "Codes de sortie : 0 complet et valide, 1 incomplet ou invalide, "
            "2 prérequis absents."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="rapport JSON sur stdout")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="n'imprime que les numéros de batch final manquants (un par ligne)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    issues = []
    if not BATCHES_DIR.is_dir():
        issues.append(f"répertoire absent : {BATCHES_DIR}")
    if not CODEBOOK_PATH.is_file():
        issues.append(f"codebook absent : {CODEBOOK_PATH}")
    if issues:
        for issue in issues:
            info(f"[erreur] {issue}")
        return 2

    slugs_by_batch = load_batch_slugs(BATCHES_DIR)
    if not slugs_by_batch:
        info(f"[erreur] aucun batch-*-slugs.txt dans {BATCHES_DIR}")
        return 2

    report = build_report(slugs_by_batch, load_valid_codes(CODEBOOK_PATH))

    if args.quiet:
        for batch in report["final_missing"]:
            print(batch)
    elif args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)

    complete = not report["final_missing"] and not report["hunter_missing"]
    return 0 if complete and not report["validation_errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
