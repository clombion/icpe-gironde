#!/usr/bin/env python3
"""
score_validation.py — Justesse du tagging et accord inter-annotateur (Phase 7).

Compare les labels humains (fichiers exportés par validation/index.html) à
la fois aux tags machine et entre eux :

  - **Justesse machine par axe** : accord machine ↔ humain (ou ↔ consensus
    des deux relecteurs quand il y en a deux).
  - **Accord inter-annotateur (Cohen's κ) par axe** : plancher subjectif
    irréductible — là où deux experts divergent, la machine ne peut pas
    être « fausse » de façon signifiante.

Axes mono-valeur : accord exact + κ. Axes multi-label : indice de Jaccard
moyen (recouvrement des ensembles de codes).

Usage :
  python3 scripts/score_validation.py validation/validation-*.json
  # sans argument : cherche validation/validation-*.json

Prérequis : validation/validation-sample.json (produit par
build_validation_sample.py, contient les tags machine).

Codes de sortie : 0 rapport produit, 2 prérequis/labels absents.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import PROJECT_ROOT  # noqa: E402

SAMPLE = PROJECT_ROOT / "validation" / "validation-sample.json"
OUT = PROJECT_ROOT / "validation" / "resultats.md"
SINGLE = ["gravity", "dynamic", "trajectory", "stage", "actor"]
MULTI = ["domains", "mechanisms", "modifiers"]


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """κ de Cohen entre deux séries de labels alignées (mono-valeur)."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for x, y in pairs if x == y) / n
    labels = {x for x, _ in pairs} | {y for _, y in pairs}
    pe = sum(
        (sum(1 for x, _ in pairs if x == c) / n) * (sum(1 for _, y in pairs if y == c) / n)
        for c in labels
    )
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def agreement_single(pred: list[str], truth: list[str]) -> float | None:
    pairs = [(p, t) for p, t in zip(pred, truth) if p is not None and t is not None]
    return sum(1 for p, t in pairs if p == t) / len(pairs) if pairs else None


def scenario(acc: float | None, human_agree: float | None, kappa: float | None) -> tuple[str, str]:
    """Classe un axe en scénario post-revue et donne l'action à suivre.

    Deux dimensions : hauteur du plafond humain (accord inter-annotateur) et
    écart de la machine sous ce plafond. La justesse machine se lit toujours
    PAR RAPPORT au plafond humain, jamais contre 100 %."""
    if acc is None:
        return ("indéterminé", "pas assez de fiches jugées en commun")
    if human_agree is None or kappa is None:
        return ("plafond inconnu (1 relecteur)",
                "ajouter un 2e relecteur — sans plafond humain, une justesse de "
                f"{acc*100:.0f}% n'est ni bonne ni mauvaise")
    gap = human_agree - acc  # écart machine sous le plafond humain
    subjective = kappa < 0.40
    ceiling_high = kappa >= 0.60

    if subjective:
        if gap <= 0.05:
            return ("axe subjectif — machine au plancher",
                    "l'axe lui-même manque de vérité stable (les experts divergent) ; "
                    "publier en signal faible avec κ divulgué, ou agréger à une échelle "
                    "plus grossière où les humains s'accordent")
        return ("axe subjectif ET machine en deçà",
                "double problème : axe ambigu + erreur machine propre ; "
                "redéfinir/clarifier le codebook de cet axe, ou l'abandonner")

    if gap <= 0.05:
        tag = "fiable — machine au plafond humain" if ceiling_high else \
              "fiable (plafond modéré) — machine au niveau humain"
        return (tag, "publier comme résultat ; le résiduel est le plancher subjectif, "
                     "pas une erreur machine")
    if gap <= 0.15:
        return ("écart corrigeable",
                "erreur machine que les humains ne partagent pas ; inspecter la matrice "
                "de confusion ci-dessous, corriger le prompt, re-taguer, re-mesurer")
    return ("cassé — erreur systématique",
            "NE PAS publier cet axe ; écart trop large pour du bruit — bug probable de "
            "prompt/taxonomie ou désaccord de définition machine↔humain ; investiguer")


def confusion(pred: list[str], truth: list[str], top: int = 6) -> list[tuple[str, str, int]]:
    """Paires (code machine, code humain, n) les plus fréquentes en désaccord."""
    from collections import Counter
    c = Counter((p, t) for p, t in zip(pred, truth)
                if p is not None and t is not None and p != t)
    return [(p, t, n) for (p, t), n in c.most_common(top)]


def main() -> int:
    if not SAMPLE.is_file():
        print(f"[erreur] échantillon absent : {SAMPLE}", file=sys.stderr)
        return 2
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    machine = {it["fiche_id"]: it["machine"] for it in sample["items"]}
    order = [it["fiche_id"] for it in sample["items"]]

    paths = sys.argv[1:] or sorted(glob.glob(str(PROJECT_ROOT / "validation" / "validation-*.json")))
    paths = [p for p in paths if Path(p).name != "validation-sample.json"]
    if not paths:
        print("[erreur] aucun fichier de labels (validation/validation-<relecteur>.json).", file=sys.stderr)
        print("         Labelliser d'abord via validation/index.html, puis exporter.", file=sys.stderr)
        return 2

    reviewers = {}
    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        reviewers[d.get("reviewer", Path(p).stem)] = d.get("decisions", d)
    names = list(reviewers)
    covered = [fid for fid in order if all(fid in reviewers[r] for r in names)]

    lines = ["# Validation Phase 7 — résultats", ""]
    lines.append(f"Échantillon : **{sample['n']}** fiches (seed {sample['seed']}). "
                 f"Relecteur·rice·s : **{', '.join(names)}**. "
                 f"Fiches jugées par tou·te·s : **{len(covered)}**.")
    lines.append("")
    lines.append(f"Stratification : gravité {sample['strata']['gravity']}, "
                 f"confiance {sample['strata']['confidence']}.")
    lines.append("")

    # Justesse machine (vs chaque relecteur)
    lines.append("## Justesse machine par axe (accord machine ↔ humain)")
    lines.append("")
    header = "| Axe | " + " | ".join(names) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(names) + 1))
    for field in SINGLE:
        cells = []
        for r in names:
            pred = [machine[f].get(field) for f in covered]
            truth = [reviewers[r].get(f, {}).get(field) for f in covered]
            a = agreement_single(pred, truth)
            cells.append(f"{a*100:.0f}%" if a is not None else "—")
        lines.append(f"| {field} | " + " | ".join(cells) + " |")
    for field in MULTI:
        cells = []
        for r in names:
            js = [jaccard(machine[f].get(field), reviewers[r].get(f, {}).get(field)) for f in covered]
            cells.append(f"J={sum(js)/len(js):.2f}" if js else "—")
        lines.append(f"| {field} (Jaccard) | " + " | ".join(cells) + " |")
    lines.append("")

    # Inter-annotateur
    if len(names) >= 2:
        a, b = names[0], names[1]
        lines.append(f"## Accord inter-annotateur {a} ↔ {b} (plancher irréductible)")
        lines.append("")
        lines.append("| Axe | Accord | κ (Cohen) |")
        lines.append("|---|---|---|")
        for field in SINGLE:
            la = [reviewers[a].get(f, {}).get(field) for f in covered]
            lb = [reviewers[b].get(f, {}).get(field) for f in covered]
            agr = agreement_single(la, lb)
            k = cohen_kappa(la, lb)
            lines.append(f"| {field} | {agr*100:.0f}% | {k:.2f} |" if k is not None else f"| {field} | — | — |")
        for field in MULTI:
            js = [jaccard(reviewers[a].get(f, {}).get(field), reviewers[b].get(f, {}).get(field)) for f in covered]
            lines.append(f"| {field} (Jaccard) | {sum(js)/len(js):.2f} | — |")
        lines.append("")
        lines.append("_κ < 0.4 accord faible · 0.4–0.6 modéré · 0.6–0.8 substantiel · > 0.8 quasi-parfait. "
                     "Un κ faible sur un axe = jugement intrinsèquement subjectif ; la justesse machine "
                     "sur cet axe doit être lue à l'aune de ce plancher, pas contre une vérité absolue._")
    else:
        lines.append("_Un seul jeu de labels : pas d'accord inter-annotateur calculable. "
                     "Ajouter un second relecteur pour mesurer le plancher subjectif par axe._")
    lines.append("")

    # Scénario + action par axe (le cœur de la décision post-revue)
    lines.append("## Scénario par axe → action")
    lines.append("")
    lines.append("| Axe | Justesse machine | Plafond humain (κ) | Scénario | Action |")
    lines.append("|---|---|---|---|---|")
    two = len(names) >= 2
    confusions: list[tuple[str, list]] = []
    for field in SINGLE:
        if two:
            a, b = names[0], names[1]
            la = [reviewers[a].get(f, {}).get(field) for f in covered]
            lb = [reviewers[b].get(f, {}).get(field) for f in covered]
            consensus = [(f, x) for f, x, y in zip(covered, la, lb) if x is not None and x == y]
            pred = [machine[f].get(field) for f, _ in consensus]
            truth = [x for _, x in consensus]
            acc = agreement_single(pred, truth)
            h_agree = agreement_single(la, lb)
            k = cohen_kappa(la, lb)
        else:
            r = names[0]
            pred = [machine[f].get(field) for f in covered]
            truth = [reviewers[r].get(f, {}).get(field) for f in covered]
            acc, h_agree, k = agreement_single(pred, truth), None, None
        label, action = scenario(acc, h_agree, k)
        acc_s = f"{acc*100:.0f}%" if acc is not None else "—"
        ceil_s = f"{h_agree*100:.0f}% (κ={k:.2f})" if k is not None else "—"
        lines.append(f"| {field} | {acc_s} | {ceil_s} | {label} | {action} |")
        if two and ("corrigeable" in label or "cassé" in label or "en deçà" in label):
            confusions.append((field, confusion(pred, truth)))
    lines.append("")
    lines.append("**Lecture.** Justesse machine lue par rapport au plafond humain, jamais contre 100 %. "
                 "Seuils : écart machine↔plafond ≤ 5 pts = _fiable_ (publier) · 5–15 pts = _corrigeable_ "
                 "(corriger le prompt, re-taguer) · > 15 pts = _cassé_ (ne pas publier, bug probable). "
                 "Indépendamment, κ < 0.40 = _axe subjectif_ (le problème est l'axe, pas la machine : "
                 "agréger ou redéfinir). 0 % d'écart = axe trivialement objectif (ou échantillon trop facile — "
                 "vérifier la strate) ; 100 % d'écart = erreur systématique certaine, à investiguer avant tout.")
    lines.append("")

    # Matrices de confusion pour les axes à corriger (le « diff » exploitable)
    if confusions:
        lines.append("## Matrices de confusion (axes corrigeables/cassés)")
        lines.append("")
        for field, pairs in confusions:
            if not pairs:
                continue
            lines.append(f"**{field}** — top désaccords machine → humain :")
            lines.append("")
            lines.append("| machine | humain | n |")
            lines.append("|---|---|---|")
            for p, t, n in pairs:
                lines.append(f"| {p} | {t} | {n} |")
            lines.append("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[write] {OUT.relative_to(PROJECT_ROOT)}")
    print("\n".join(lines[:14]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
