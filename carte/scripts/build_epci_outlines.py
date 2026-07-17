#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Fetch EPCI contours for every EPCI present in Gironde and write them as a
single GeoJSON FeatureCollection at
``carte/data/gironde-epci-outlines.geojson``.

The list of SIREN codes is read from
``carte/data/gironde-commune-epci.json`` (produced by
scripts/enrichir_libelles.py), so this script only needs to run when the
set of EPCIs changes.

Uses geo.api.gouv.fr (Etalab, CORS-OK, no auth). Each EPCI contour is
fetched individually — batching isn't supported by the API — but there
are only 28 EPCIs in Gironde and the total stays well under 500 KB.

Run from the repo root:

    uv run carte/scripts/build_epci_outlines.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOOKUP_PATH = REPO_ROOT / "carte" / "data" / "gironde-commune-epci.json"
OUT_PATH = REPO_ROOT / "carte" / "data" / "gironde-epci-outlines.geojson"

EPCI_URL = (
    "https://geo.api.gouv.fr/epcis/{code}"
    "?fields=nom,code,contour&format=geojson&geometry=contour"
)


def fetch_epci_contour(siren: str) -> dict:
    url = EPCI_URL.format(code=siren)
    req = urllib.request.Request(url, headers={"User-Agent": "build_epci_outlines.py"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def main() -> int:
    if not LOOKUP_PATH.exists():
        print(
            f"ERROR: {LOOKUP_PATH} missing — run scripts/enrichir_libelles.py "
            f"first to populate the commune/EPCI cache.",
            file=sys.stderr,
        )
        return 1

    with LOOKUP_PATH.open(encoding="utf-8") as f:
        lookup = json.load(f)

    # Unique SIREN codes in Gironde
    sirens = sorted({
        info.get("epci_siren")
        for info in lookup.values()
        if info.get("epci_siren")
    })
    print(f"EPCIs to fetch: {len(sirens)}", file=sys.stderr)

    features = []
    for siren in sirens:
        print(f"  fetching {siren}...", file=sys.stderr)
        feat = fetch_epci_contour(siren)
        if feat.get("type") != "Feature":
            print(f"    WARNING: unexpected response for {siren}", file=sys.stderr)
            continue
        # Normalise properties: keep only what we need, rename for clarity
        props = feat.get("properties") or {}
        feat["properties"] = {
            "siren": siren,
            "nom": props.get("nom"),
        }
        feat.pop("id", None)
        features.append(feat)

    fc = {"type": "FeatureCollection", "features": features}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))

    size = OUT_PATH.stat().st_size
    print(
        f"\nwrote {OUT_PATH.relative_to(REPO_ROOT)}: "
        f"{len(features)} EPCIs, {size:,} bytes",
        file=sys.stderr,
    )
    print(json.dumps({"ok": True, "count": len(features), "size": size}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
