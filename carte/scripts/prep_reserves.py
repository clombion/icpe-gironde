#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Fetch RNN and RNR GeoJSON from the IGN Géoplateforme WFS, filter to the
Gironde bbox, normalize properties, and write the two committed output files.

Run from the repo root:

    uv run carte/scripts/prep_reserves.py

No external dependencies — standard library only. Bbox intersection is done
via coordinate iteration (sufficient for keeping/dropping whole features).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

# Gironde bounding box (conservative envelope)
LON_MIN, LON_MAX = -1.3, 0.3
LAT_MIN, LAT_MAX = 44.2, 45.6

WFS = "https://data.geopf.fr/wfs/ows"
WFS_PARAMS = "service=WFS&version=2.0.0&request=GetFeature&outputFormat=application/json&srsName=EPSG:4326"

SOURCES = [
    (
        "rnn",
        "reserves-naturelles-nationales.geojson",
        f"{WFS}?{WFS_PARAMS}&typeNames=patrinat_rnn:rnn",
    ),
    (
        "rnr",
        "reserves-naturelles-regionales.geojson",
        f"{WFS}?{WFS_PARAMS}&typeNames=patrinat_rnr:rnr",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "carte" / "data"


def iter_coords(geom: dict):
    """Yield (lon, lat) pairs from any GeoJSON geometry."""
    if geom is None:
        return
    t = geom.get("type")
    c = geom.get("coordinates")
    if c is None:
        return
    if t == "Point":
        yield c[0], c[1]
    elif t in ("MultiPoint", "LineString"):
        for p in c:
            yield p[0], p[1]
    elif t in ("MultiLineString", "Polygon"):
        for ring in c:
            for p in ring:
                yield p[0], p[1]
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for p in ring:
                    yield p[0], p[1]


def intersects_gironde(geom: dict) -> bool:
    """Return True if any coordinate of the geometry falls inside the bbox."""
    for lon, lat in iter_coords(geom):
        if LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX:
            return True
    return False


def normalize_props(src: dict) -> dict:
    """Map IGN source fields to our target schema."""
    return {
        "nom": src.get("nom_site") or src.get("nom") or "Sans nom",
        "id_local": src.get("id_local"),
        "id_mnhn": src.get("id_mnhn"),
        "date_crea": src.get("date_crea"),
        "url_fiche": src.get("url_fiche"),
        "surf_ha": src.get("surf_off"),
        "operateur": src.get("operateur"),
        "gest_site": src.get("gest_site"),
    }


def fetch(url: str) -> dict:
    print(f"  fetching {url[:90]}...", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "prep_reserves.py"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def process(key: str, out_name: str, url: str) -> dict:
    print(f"\n[{key.upper()}]", file=sys.stderr)
    data = fetch(url)
    total = len(data.get("features", []))
    kept = []
    for feat in data.get("features", []):
        if intersects_gironde(feat.get("geometry") or {}):
            feat["properties"] = normalize_props(feat.get("properties") or {})
            # drop WFS-specific metadata we don't need
            feat.pop("id", None)
            kept.append(feat)
    out = {"type": "FeatureCollection", "features": kept}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / out_name
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size = out_path.stat().st_size
    print(
        f"  total features: {total}  →  kept (Gironde bbox): {len(kept)}  →  {size:,} bytes",
        file=sys.stderr,
    )
    for feat in kept:
        p = feat["properties"]
        print(f"    · {p['nom']} — {p.get('date_crea','?')}", file=sys.stderr)
    return {"key": key, "out": str(out_path), "total": total, "kept": len(kept), "size": size}


def main() -> int:
    results = [process(*s) for s in SOURCES]
    print(
        "\n" + json.dumps({"ok": True, "outputs": results}, indent=2),
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
