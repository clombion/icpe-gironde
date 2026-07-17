#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
build_metadata_samples.py — Génère metadonnees_samples.json depuis le dictionnaire.

Lit ``carte/data/metadonnees_colonnes.csv`` pour découvrir la liste des
fichiers CSV documentés (auto-discover via la colonne ``fichier``), puis
scanne chacun pour inférer le type de chaque colonne et choisir jusqu'à
5 valeurs représentatives. Le résultat est sérialisé en JSON dans
``carte/data/metadonnees_samples.json``.

Le fichier produit est consommé par la page ``/donnees/`` (Phase 4 du
plan d'audit) qui le couple aux définitions du dictionnaire pour
afficher des "puces d'échantillon" cliquables-pour-copier sous chaque
définition de colonne.

Inférence de type :
  - boolean   : valeurs ⊆ {TRUE, FALSE, True, False, true, false, 0, 1}
  - numeric   : tout parse comme float (sous-classification int/float)
  - date      : tout matche ``\\d{4}-\\d{2}-\\d{2}([T ]…)?``
  - identifier: distinct_count == non_null_count ET distinct_count > 20
  - categorical: distinct_count ≤ 20
  - text      : sinon

Sélection d'échantillons :
  - boolean / categorical : top-5 most_common (frequency-weighted)
  - numeric (int)         : min, q1, median, q3, max — formatés en int
  - numeric (float)       : min, q1, median, q3, max — formatés en float
  - date                  : min, q1, median, q3, max
  - identifier / text     : 5 valeurs aux positions [0, n/5, 2n/5, 3n/5, 4n/5]

Aucun pré-requis : stdlib uniquement (csv, json, re, collections,
pathlib). Lance avec ``uv run scripts/build_metadata_samples.py``.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import TypedDict

# Le module _paths et le helper _metadonnees_util sont au même niveau que ce script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _metadonnees_util import atomic_write  # noqa: E402
from _paths import (  # noqa: E402
    CARTE_DATA_DIR,
    CARTE_ENRICHI_CSV,
    CARTE_METADATA_CSV,
    CARTE_RAPPORTS_CSV,
    DONNEES_AUDIT_DIR,
    PROJECT_ROOT,
)

OUTPUT_PATH = CARTE_DATA_DIR / "metadonnees_samples.json"

# CSV files that the data dictionary should describe. The metadata CSV
# discovers them via its `fichier` column, but each fichier value is just
# a basename — we need to know where to look for the actual file. This
# table maps basename → on-disk path. Paths come from _paths.py to keep
# the single-source-of-truth invariant — never reconstruct them inline.
KNOWN_FILE_LOCATIONS: dict[str, Path] = {
    "liste-icpe-gironde_enrichi.csv": CARTE_ENRICHI_CSV,
    "rapports-inspection.csv": CARTE_RAPPORTS_CSV,
    "coordonnees-audit-full.csv": DONNEES_AUDIT_DIR / "coordonnees-audit-full.csv",
}


# --- Output shapes ------------------------------------------------------

class ColumnStats(TypedDict):
    """Per-column statistics produced by ``scan_csv``."""

    type: str  # boolean | numeric | date | identifier | categorical | text
    distinct_count: int
    non_null_count: int
    samples: list[str]


class CsvScanResult(TypedDict):
    """Top-level result of ``scan_csv``."""

    row_count: int
    columns: dict[str, ColumnStats]

DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?)?$"
)
BOOLEAN_VALUES = {"TRUE", "FALSE", "True", "False", "true", "false", "0", "1"}
MAX_SAMPLES = 5


# --- Type inference -----------------------------------------------------

def is_int_like(values: list[str]) -> bool:
    """True if every non-empty value parses cleanly as int."""
    saw_one = False
    for v in values:
        if not v:
            continue
        try:
            int(v)
        except ValueError:
            return False
        saw_one = True
    return saw_one


def is_numeric_like(values: list[str]) -> bool:
    """True if every non-empty value parses cleanly as float."""
    saw_one = False
    for v in values:
        if not v:
            continue
        try:
            float(v)
        except ValueError:
            return False
        saw_one = True
    return saw_one


def is_boolean_like(values: list[str]) -> bool:
    """True if every non-empty value is in BOOLEAN_VALUES."""
    saw_one = False
    for v in values:
        if not v:
            continue
        if v not in BOOLEAN_VALUES:
            return False
        saw_one = True
    return saw_one


def is_date_like(values: list[str]) -> bool:
    """True if every non-empty value matches the ISO date regex."""
    saw_one = False
    for v in values:
        if not v:
            continue
        if not DATE_RE.match(v):
            return False
        saw_one = True
    return saw_one


def infer_type(values: list[str], distinct_count: int, non_null_count: int) -> str:
    """Returns 'numeric', 'integer', 'boolean', 'date', 'identifier',
    'categorical', or 'text'."""
    if non_null_count == 0:
        return "text"
    if is_boolean_like(values):
        return "boolean"
    if is_int_like(values):
        return "integer"
    if is_numeric_like(values):
        return "numeric"
    if is_date_like(values):
        return "date"
    if distinct_count == non_null_count and distinct_count > 20:
        return "identifier"
    if distinct_count <= 20:
        return "categorical"
    return "text"


# --- Sample selection ---------------------------------------------------

def positional_samples(values: list[str], k: int = MAX_SAMPLES) -> list[str]:
    """Returns k values at evenly-spaced positions, deterministic across runs."""
    n = len(values)
    if n == 0:
        return []
    if n <= k:
        return list(values)
    return [values[i * n // k] for i in range(k)]


def numeric_quantile_samples(values: list[str], as_int: bool) -> list[str]:
    """Returns [min, q1, median, q3, max] formatted appropriately."""
    if as_int:
        nums = sorted(int(v) for v in values if v)
        if not nums:
            return []
        n = len(nums)
        picks = [
            nums[0],
            nums[n // 4],
            nums[n // 2],
            nums[3 * n // 4],
            nums[-1],
        ]
        return [str(x) for x in picks]
    nums = sorted(float(v) for v in values if v)
    if not nums:
        return []
    n = len(nums)
    picks = [
        nums[0],
        nums[n // 4],
        nums[n // 2],
        nums[3 * n // 4],
        nums[-1],
    ]
    # Use repr() for floats to keep precision but avoid trailing zeros
    return [repr(x) for x in picks]


def date_quantile_samples(values: list[str]) -> list[str]:
    """Returns [min, q1, median, q3, max] dates as ISO strings."""
    sorted_dates = sorted({v for v in values if v})
    if not sorted_dates:
        return []
    n = len(sorted_dates)
    if n <= MAX_SAMPLES:
        return sorted_dates
    return [
        sorted_dates[0],
        sorted_dates[n // 4],
        sorted_dates[n // 2],
        sorted_dates[3 * n // 4],
        sorted_dates[-1],
    ]


def select_samples(values: list[str], type_: str) -> list[str]:
    """Choose representative samples based on the inferred type."""
    if type_ in ("categorical", "boolean"):
        return [v for v, _ in Counter(v for v in values if v).most_common(MAX_SAMPLES)]
    if type_ == "integer":
        return numeric_quantile_samples(values, as_int=True)
    if type_ == "numeric":
        return numeric_quantile_samples(values, as_int=False)
    if type_ == "date":
        return date_quantile_samples(values)
    # identifier, text
    non_null = [v for v in values if v]
    return positional_samples(non_null)


# --- CSV scanning -------------------------------------------------------

def scan_csv(path: Path, delimiter: str = ",") -> CsvScanResult:
    """Scan a CSV file, return a CsvScanResult.

    Each column is profiled into a ColumnStats: inferred type
    (boolean / numeric / date / identifier / categorical / text),
    distinct/non-null counts, and up to MAX_SAMPLES sample values
    chosen with a strategy that depends on the inferred type.
    """
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    columns: dict[str, ColumnStats] = {}
    for col in fieldnames:
        values = [row.get(col, "") or "" for row in rows]
        non_null = [v for v in values if v]
        distinct = set(non_null)
        non_null_count = len(non_null)
        distinct_count = len(distinct)

        type_ = infer_type(values, distinct_count, non_null_count)
        samples = select_samples(values, type_)

        columns[col] = ColumnStats(
            type=type_,
            distinct_count=distinct_count,
            non_null_count=non_null_count,
            samples=samples,
        )

    return CsvScanResult(row_count=len(rows), columns=columns)


def detect_delimiter(path: Path) -> str:
    """Sniff the delimiter from the first line. Defaults to ','."""
    with path.open(encoding="utf-8", newline="") as f:
        first_line = f.readline()
    if ";" in first_line and "," not in first_line:
        return ";"
    if first_line.count(";") > first_line.count(","):
        return ";"
    return ","


# --- Main ---------------------------------------------------------------

def discover_files(metadata_path: Path) -> list[str]:
    """Returns distinct `fichier` values from metadonnees_colonnes.csv."""
    seen: list[str] = []
    seen_set: set[str] = set()
    with metadata_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            f_name = row.get("fichier", "")
            if f_name and f_name not in seen_set:
                seen.append(f_name)
                seen_set.add(f_name)
    return seen


def main() -> int:
    if not CARTE_METADATA_CSV.exists():
        print(
            f"[error] {CARTE_METADATA_CSV.relative_to(PROJECT_ROOT)} introuvable",
            file=sys.stderr,
        )
        return 2

    files = discover_files(CARTE_METADATA_CSV)
    print(f"[samples] {len(files)} fichier(s) découvert(s) dans le dictionnaire")

    output: dict[str, dict] = {}
    for basename in files:
        path = KNOWN_FILE_LOCATIONS.get(basename)
        if path is None:
            print(
                f"[samples] {basename}: chemin inconnu (ajouter à "
                f"KNOWN_FILE_LOCATIONS), skip",
                file=sys.stderr,
            )
            continue
        if not path.exists():
            print(
                f"[samples] {basename}: {path.relative_to(PROJECT_ROOT)} introuvable, skip",
                file=sys.stderr,
            )
            continue

        delimiter = detect_delimiter(path)
        try:
            scanned = scan_csv(path, delimiter=delimiter)
        except Exception as exc:  # noqa: BLE001
            print(f"[samples] {basename}: erreur de parsing : {exc}", file=sys.stderr)
            continue

        output[basename] = scanned
        print(
            f"[samples] {basename}: {scanned['row_count']} lignes, "
            f"{len(scanned['columns'])} colonnes"
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(OUTPUT_PATH) as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[samples] écrit {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
