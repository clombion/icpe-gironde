#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
apply_corrections.py — Compile audit review decisions into a sidecar CSV.

Reads the flagged.json produced by audit_coordinates.py and any review
files committed into the coordonnees-audit-reviews/ directory, then
writes a single coordonnees-corrections.csv that enrichir_libelles.py
consumes to patch map coordinates.

Assumptions:
  - coordonnees-audit-flagged.json exists (exit 2 if not).
  - coordonnees-audit-reviews/ may not exist yet (exit 0, no reviews).
  - Review files are bucket-*.json, as produced by the audit UI.
  - Each review file has: flagged_hash, group, bucket_index, reviewer, decisions.
  - Each decision has: id_icpe, verdict, note, pertinent_enquete.
  - placer_manuellement decisions may carry manual_lat / manual_lon.

Usage:
    python3 scripts/apply_corrections.py               # write CSV + re-run enricher
    python3 scripts/apply_corrections.py --dry-run      # preview only
    python3 scripts/apply_corrections.py --no-enrich    # write CSV without re-running enricher
    python3 scripts/apply_corrections.py --help

Exit codes:
    0  success (including "no reviews" early exit)
    1  runtime error
    2  missing prerequisite (flagged.json absent)
"""

from __future__ import annotations

import argparse
import subprocess
import csv
import io
import json
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _metadonnees_util import atomic_write  # noqa: E402
from _paths import (  # noqa: E402
    CORRECTIONS_CSV,
    DONNEES_AUDIT_REVIEWS_DIR,
    FLAGGED_JSON_PATH,
    PROJECT_ROOT,
    SCRIPTS_DIR,
)
from _verdicts import Verdict  # noqa: E402

# --- Types ----------------------------------------------------------------


class CorrectionRow(TypedDict):
    id_icpe: str
    verdict: str
    new_lat: str
    new_lon: str
    reviewer: str
    note: str
    pertinent_enquete: str


CSV_COLUMNS = list(CorrectionRow.__annotations__)

# --- Pure business logic --------------------------------------------------


def load_flagged_index(data: dict) -> dict[str, dict]:
    """Build id_icpe -> FlaggedItem index from parsed flagged.json."""
    index: dict[str, dict] = {}
    for group in data.get("groups", []):
        for item in group.get("items", []):
            id_icpe = item.get("id_icpe", "")
            if id_icpe:
                index[id_icpe] = item
    return index


def validate_review_file(
    data: dict,
    flagged_index: dict[str, dict],
) -> tuple[list[dict], list[str]]:
    """Validate a single parsed review file.

    Returns (valid_decisions, warnings).
    - Checks required fields: reviewer, decisions.
    - Checks each decision has id_icpe and a valid verdict.
    - Orphaned id_icpe (not in flagged_index) -> warning + skip.
    """
    warnings: list[str] = []
    valid: list[dict] = []

    required_top = ["reviewer", "decisions"]
    for field in required_top:
        if field not in data:
            return [], [f"missing required field: {field}"]

    reviewer = data["reviewer"]
    decisions = data["decisions"]
    if not isinstance(decisions, list):
        return [], ["decisions is not a list"]

    for i, dec in enumerate(decisions):
        if not isinstance(dec, dict):
            warnings.append(f"decision[{i}] is not an object, skipped")
            continue
        id_icpe = dec.get("id_icpe")
        if not isinstance(id_icpe, str) or not id_icpe:
            warnings.append(f"decision[{i}] missing id_icpe, skipped")
            continue
        verdict_raw = dec.get("verdict", "")
        try:
            Verdict(verdict_raw)
        except ValueError:
            warnings.append(
                f"decision[{i}] id_icpe={id_icpe}: unknown verdict "
                f"{verdict_raw!r}, skipped"
            )
            continue
        if id_icpe not in flagged_index:
            warnings.append(
                f"decision[{i}] id_icpe={id_icpe}: orphaned (not in "
                f"flagged.json), skipped"
            )
            continue
        valid.append({**dec, "_reviewer": reviewer})

    return valid, warnings


def build_corrections(
    decisions_by_id: dict[str, dict],
    flagged_index: dict[str, dict],
) -> list[CorrectionRow]:
    """Build correction rows from validated decisions.

    For each decision:
      - garder_stored    -> row with empty new_lat/new_lon
      - terrain          -> row with empty new_lat/new_lon
      - utiliser_geocoded -> look up geocoded_lat/geocoded_lon from flagged item
      - placer_manuellement -> read manual_lat/manual_lon from decision
    """
    rows: list[CorrectionRow] = []
    warn_count = 0

    for id_icpe in sorted(decisions_by_id):
        dec = decisions_by_id[id_icpe]
        verdict = dec.get("verdict", "")
        reviewer = dec.get("_reviewer", "")
        note = dec.get("note", "")
        pertinent = dec.get("pertinent_enquete", False)
        pertinent_str = "true" if pertinent else "false"

        new_lat = ""
        new_lon = ""

        if verdict == Verdict.UTILISER_GEOCODED:
            flagged_item = flagged_index.get(id_icpe, {})
            geo_lat = flagged_item.get("geocoded_lat")
            geo_lon = flagged_item.get("geocoded_lon")
            if geo_lat is not None and geo_lon is not None:
                new_lat = str(geo_lat)
                new_lon = str(geo_lon)
            else:
                print(
                    f"[warn] {id_icpe}: utiliser_geocoded but flagged item "
                    f"has no geocoded coords — row will have empty coords",
                    file=sys.stderr,
                )
                warn_count += 1

        elif verdict == Verdict.PLACER_MANUELLEMENT:
            m_lat = dec.get("manual_lat")
            m_lon = dec.get("manual_lon")
            if m_lat is not None and m_lon is not None:
                new_lat = str(m_lat)
                new_lon = str(m_lon)
            else:
                print(
                    f"[warn] {id_icpe}: placer_manuellement but decision "
                    f"missing manual_lat/manual_lon — row will have empty coords",
                    file=sys.stderr,
                )
                warn_count += 1

        # garder_stored and terrain: new_lat/new_lon stay empty

        rows.append(CorrectionRow(
            id_icpe=id_icpe,
            verdict=verdict,
            new_lat=new_lat,
            new_lon=new_lon,
            reviewer=reviewer,
            note=note,
            pertinent_enquete=pertinent_str,
        ))

    return rows


# --- I/O functions --------------------------------------------------------


def load_flagged() -> dict:
    """Read and parse FLAGGED_JSON_PATH."""
    with FLAGGED_JSON_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def scan_reviews() -> list[tuple[str, dict]]:
    """Glob review files, parse each, return list of (filename, data).

    Invalid JSON files produce a warning and are skipped.
    Files are sorted by name for deterministic last-wins ordering.
    """
    if not DONNEES_AUDIT_REVIEWS_DIR.exists():
        return []

    results: list[tuple[str, dict]] = []
    for path in sorted(DONNEES_AUDIT_REVIEWS_DIR.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[warn] {path.name}: invalid JSON ({exc}), skipped",
                file=sys.stderr,
            )
            continue
        results.append((path.name, data))
    return results


def write_corrections(
    rows: list[CorrectionRow],
    path: Path,
    dry_run: bool,
) -> None:
    """Write correction rows to CSV (atomic), or print to stdout if dry_run."""
    if dry_run:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        print(buf.getvalue(), end="")
        return

    with atomic_write(path) as h:
        writer = csv.DictWriter(h, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    print(f"[corrections] wrote {rel} ({len(rows)} rows)")


# --- Main -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="apply_corrections",
        description=(
            "Compile audit review decisions into coordonnees-corrections.csv."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/apply_corrections.py              # write + enrich\n"
            "  python3 scripts/apply_corrections.py --dry-run    # preview only\n"
            "  python3 scripts/apply_corrections.py --no-enrich  # write CSV only\n"
            "\n"
            "Exit codes:\n"
            "  0  success\n"
            "  1  runtime error (including enricher failure)\n"
            "  2  missing prerequisite (flagged.json absent)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="preview the CSV to stdout without writing to disk",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="skip the automatic re-run of enrichir_libelles.py after writing the sidecar",
    )
    args = parser.parse_args()

    # --- Startup validation -----------------------------------------------

    if not FLAGGED_JSON_PATH.exists():
        print(
            f"[corrections] prerequisite missing: {FLAGGED_JSON_PATH.name}",
            file=sys.stderr,
        )
        return 2

    review_files = scan_reviews()
    if not review_files:
        print("[corrections] no review files found — nothing to do")
        return 0

    # --- Load & index flagged data ----------------------------------------

    try:
        flagged_data = load_flagged()
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[corrections] failed to load flagged.json: {exc}", file=sys.stderr)
        return 1

    flagged_index = load_flagged_index(flagged_data)

    # --- Validate reviews & collect decisions ------------------------------

    all_decisions: dict[str, dict] = {}  # id_icpe -> decision (last wins)
    total_files = 0
    total_orphaned = 0
    total_errors = 0
    all_warnings: list[str] = []

    for filename, data in review_files:
        total_files += 1
        valid_decisions, warnings = validate_review_file(data, flagged_index)

        for w in warnings:
            all_warnings.append(f"{filename}: {w}")

        if not valid_decisions and warnings:
            # Count files that had only warnings (no usable decisions) as errors
            # only if the file had structural problems (missing fields)
            if any("missing required field" in w for w in warnings):
                total_errors += 1

        for dec in valid_decisions:
            all_decisions[dec["id_icpe"]] = dec

    # Count orphaned warnings
    for w in all_warnings:
        if "orphaned" in w:
            total_orphaned += 1

    # Print warnings
    for w in all_warnings:
        print(f"[warn] {w}", file=sys.stderr)

    # --- Build corrections ------------------------------------------------

    corrections = build_corrections(all_decisions, flagged_index)

    # --- Write output -----------------------------------------------------

    write_corrections(corrections, CORRECTIONS_CSV, args.dry_run)

    # --- Summary ----------------------------------------------------------

    n_with_coords = sum(1 for r in corrections if r["new_lat"] and r["new_lon"])
    n_kept = sum(1 for r in corrections if r["verdict"] == Verdict.GARDER_STORED)
    n_terrain = sum(1 for r in corrections if r["verdict"] == Verdict.TERRAIN)

    print(
        f"[corrections] summary: "
        f"{total_files} review file(s), "
        f"{len(all_decisions)} decision(s), "
        f"{len(corrections)} correction(s) "
        f"({n_with_coords} with coords, "
        f"{n_kept} kept, "
        f"{n_terrain} deferred/terrain, "
        f"{total_orphaned} orphaned, "
        f"{total_errors} error(s))"
    )

    # --- Re-run enricher to apply corrections to the map CSV ---------------

    if not args.dry_run and not args.no_enrich and n_with_coords > 0:
        enricher = SCRIPTS_DIR / "enrichir_libelles.py"
        if enricher.exists():
            print(f"[corrections] relance de enrichir_libelles.py pour appliquer les corrections…")
            result = subprocess.run(
                [sys.executable, str(enricher)],
                cwd=str(PROJECT_ROOT),
            )
            if result.returncode != 0:
                print(
                    f"[corrections] enrichir_libelles.py a échoué (exit {result.returncode})",
                    file=sys.stderr,
                )
                return 1
            print("[corrections] carte mise à jour avec les coordonnées corrigées")
        else:
            print(
                f"[warn] {enricher.relative_to(PROJECT_ROOT)} introuvable — "
                f"relance manuellement pour appliquer les corrections",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
