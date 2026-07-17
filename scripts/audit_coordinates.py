#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///
"""
audit_coordinates.py — Audit des écarts entre coordonnées et adresses ICPE.

Pour chaque installation classée du département de la Gironde, compare la
position enregistrée dans Géorisques (latitude/longitude) à la position
dérivée de l'adresse postale via le géocodeur national BAN
(api-adresse.data.gouv.fr). Le but est d'identifier les sites où le
désaccord change la réponse à la question « ce site est-il dans une
réserve naturelle ? » — cas critique pour l'enquête ICPE en réserve.

Produit trois artefacts dans données-georisques/audit/ :

  - coordonnees-audit-full.csv     (toutes les installations + colonnes audit)
  - coordonnees-audit-summary.md   (bilan lisible : histogrammes, top offenders)
  - coordonnees-audit-flagged.json (sous-ensemble pour l'outil de revue web)

Cinq passes de signaux :

  1. sentinels      — null_island, outside_gironde, commune_centroid,
                      duplicate_coords (purement offline)
  2. commune PIP    — point-in-polygon contre carte/data/gironde-communes.geojson
                      (tristate true/false/null pour les communes inconnues)
  3. BAN forward    — POST batch à api-adresse.data.gouv.fr/search/csv/
  4. BAN reverse    — POST batch à api-adresse.data.gouv.fr/reverse/csv/
  5. reserves       — point-in-polygon contre RNN/RNR + distance à la limite

Classification ladder (priorité, premier match) :

  null_island > outside_gironde > wrong_commune > address_unresolvable
  > address_imprecise > very_severe (≥2km) > severe (500-2km)
  > suspicious (100-500m) > minor (25-100m) > ok

Group assignment (3 buckets pour l'outil de revue) :

  reserves : reserve_ambiguous OR (reserve_boundary_proximity AND
             forward_distance_m is not None and forward_distance_m > 25)
  grand    : null_island | outside_gironde | wrong_commune
             | address_unresolvable | address_imprecise | very_severe | severe
  petit    : suspicious | minor

CLI :

  uv run scripts/audit_coordinates.py \\
    [--minor-m 25] [--suspicious-m 100] [--severe-m 500] [--very-severe-m 2000] \\
    [--score-cutoff 0.4] [--bucket-size 25]

Dépendance unique : requests (pour l'upload multipart à BAN). L'import est
**lazy** (à l'intérieur de post_with_retry) pour permettre aux tests de
charger ce module via scripts/tests/_loader.py sans installer requests
dans le Python système.
"""

from __future__ import annotations

import argparse
import csv
import enum
import hashlib
import io
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

# Le helper _metadonnees_util et le module _paths sont au même niveau que ce script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _metadonnees_util import (  # noqa: E402
    METADATA_SCHEMA,
    atomic_write,
    load_metadata,
    merge_metadata,
    normalize_aiot,
    require_columns,
)
from _paths import (  # noqa: E402
    CARTE_DATA_DIR,
    CARTE_METADATA_CSV,
    DONNEES_AUDIT_CACHE_DIR,
    DONNEES_AUDIT_DIR,
    DONNEES_BULK_ENRICHI_CSV,
    FLAGGED_JSON_PATH,
    PROJECT_ROOT,
)

# --- Constants & enums --------------------------------------------------

OWNER_FICHIER = "coordonnees-audit-full.csv"
FULL_CSV_PATH = DONNEES_AUDIT_DIR / "coordonnees-audit-full.csv"
SUMMARY_MD_PATH = DONNEES_AUDIT_DIR / "coordonnees-audit-summary.md"

BAN_FORWARD_URL = "https://api-adresse.data.gouv.fr/search/csv/"
BAN_REVERSE_URL = "https://api-adresse.data.gouv.fr/reverse/csv/"
BAN_CACHE_REVERSE = DONNEES_AUDIT_CACHE_DIR / "ban-reverse.csv"
# Forward geocoding uses a 5-layer cascade with per-layer cache files:
#   ban-forward-adresse1.csv  → strategy 1: adresse1 alone via BAN
#   ban-forward-adresse2.csv  → strategy 2: adresse2 alone via BAN
#   ban-forward-combined.csv  → strategy 3: adresse1 + adresse2 via BAN
#   opencage-fallback.json    → strategy 4: OpenCage geocoder (free tier
#                               2500/day, allows bulk, requires API key)
#   nominatim-fallback.json   → strategy 5: OSM/Nominatim — only for
#                               whatever OpenCage couldn't match
#                               (small residue, fits "smaller one-time
#                               bulk task" carve-out of OSM policy)
# Each layer is keyed on the sha256 of its own input CSV (or per-row
# query string for layers 4-5), so re-runs are fast and incremental.

# OpenCage (https://opencagedata.com) — primary 3rd-party fallback for
# rows BAN can't match. Aggregates OSM, GeoNames, and other sources.
# Free tier: 2500 requests/day, 1 req/sec, allows bulk geocoding
# explicitly. Requires the OPENCAGE_API_KEY environment variable.
OPENCAGE_API_KEY_ENV = "OPENCAGE_API_KEY"
OPENCAGE_URL = "https://api.opencagedata.com/geocode/v1/json"
OPENCAGE_RATE_LIMIT_SEC = 1.05
OPENCAGE_CACHE = DONNEES_AUDIT_CACHE_DIR / "opencage-fallback.json"

# Nominatim (OpenStreetMap public instance) — used as the LAST resort
# for whatever OpenCage couldn't match. Per Nominatim's policy:
# max 1 req/sec, identify with a User-Agent, cache results, and
# "smaller one-time bulk tasks may be permissible". By placing
# Nominatim AFTER the OpenCage cascade, we keep its usage minimal:
# typically dozens of requests, not hundreds.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = (
    "projet-icpe-ijba-audit/1.0 "
    "(https://github.com/bononlouis-del/Les-ICPE-en-r-serve-naturelle-nationale)"
)
NOMINATIM_RATE_LIMIT_SEC = 1.05  # generous safety margin over 1 req/s
NOMINATIM_CACHE = DONNEES_AUDIT_CACHE_DIR / "nominatim-fallback.json"

# --- Geometry thresholds (not CLI-tunable, just named so they're greppable) -

# pass_1_sentinels: a stored point within this distance of its declared
# commune's centroid is the geocoder-fallback signature ("the geocoder
# couldn't find the address, so it dropped a pin on the city hall").
COMMUNE_CENTROID_RADIUS_M = 50.0

# pass_5_reserves: a stored point within this distance of a reserve
# polygon boundary is flagged as `reserve_boundary_proximity` so the
# reviewer can manually adjudicate the in/out call instead of trusting
# the polygon side.
RESERVE_BOUNDARY_PROXIMITY_M = 200.0

# Required columns when reading the bulk-enriched CSV at boundary.
BULK_ENRICHED_REQUIRED_COLUMNS: set[str] = {
    "codeAiot", "raisonSociale", "adresse1", "adresse2", "adresse3",
    "codePostal", "codeInsee", "commune", "longitude", "latitude",
    "regimeVigueur", "statutSeveso", "ied", "prioriteNationale",
    "structure", "etablissement", "libelle_complet", "nom_commune",
    "epci_siren", "epci_nom", "url",
}


class AuditClass(enum.StrEnum):
    OK = "ok"
    MINOR = "minor"
    SUSPICIOUS = "suspicious"
    SEVERE = "severe"
    VERY_SEVERE = "very_severe"
    NULL_ISLAND = "null_island"
    OUTSIDE_GIRONDE = "outside_gironde"
    WRONG_COMMUNE = "wrong_commune"
    # Split (option B from verification): when forward fails, check reverse
    # to differentiate "stored coords are at least in the right commune"
    # from "no useful data either way".
    ADDRESS_UNRESOLVABLE_COMMUNE_OK = "address_unresolvable_commune_ok"
    ADDRESS_UNRESOLVABLE_ISOLATED = "address_unresolvable_isolated"
    ADDRESS_IMPRECISE = "address_imprecise"


class AuditGroup(enum.StrEnum):
    RESERVES = "reserves"
    GRAND = "grand"
    PETIT = "petit"


# --- TypedDicts ---------------------------------------------------------

class Thresholds(TypedDict):
    minor_m: float
    suspicious_m: float
    severe_m: float
    very_severe_m: float
    score_cutoff: float


class AuditedRow(TypedDict, total=False):
    """A bulk-enriched row + audit columns added by passes 1-5.

    The dict carries both the original Géorisques fields (codeAiot,
    raisonSociale, adresse1/2/3, longitude, latitude, etc.) and the
    audit-specific columns produced by classify() and the signal passes.
    """

    # Identity
    codeAiot: str
    id_icpe: str
    raisonSociale: str
    libelle_complet: str
    numeroSiret: str

    # Address (from bulk)
    adresse1: str
    adresse2: str
    adresse3: str
    codePostal: str
    codeInsee: str
    commune: str
    nom_commune: str
    epci_siren: str
    epci_nom: str

    # Geo (stored)
    longitude: str
    latitude: str
    stored_lat: float | None
    stored_lon: float | None

    # Géorisques metadata
    regimeVigueur: str
    statutSeveso: str
    prioriteNationale: str
    ied: str
    url: str

    # Audit signals (passes 1-5)
    sentinel_flags: str  # comma-separated
    pip_in_commune: bool | None  # tristate true/false/null

    # Forward geocode (BAN)
    forward_lat: float | None
    forward_lon: float | None
    forward_score: float | None
    forward_type: str | None
    forward_label: str | None
    forward_citycode: str | None
    forward_distance_m: float | None
    forward_error: str  # empty on success

    # Reverse geocode (BAN)
    reverse_label: str | None
    reverse_citycode: str | None

    # Reserve checks
    stored_in_reserve: str  # reserve name or 'none'
    geocoded_in_reserve: str
    stored_reserve_distance_m: float | None
    geocoded_reserve_distance_m: float | None
    reserve_ambiguous: bool
    reserve_boundary_proximity: bool

    # Final classification — typed as the StrEnums themselves so a typo
    # like row["audit_class"] = "verysever" is caught by a type checker.
    # StrEnum IS a str at runtime, so no .value coercion is needed on
    # write (and assigning .value would trip the type check).
    # AuditGroup is widened with `str` to cover the empty-string sentinel
    # used for un-flagged rows that classify() considers "not interesting".
    audit_class: AuditClass
    audit_group: AuditGroup | str


class FlaggedItem(TypedDict):
    id_icpe: str
    nom_complet: str
    siret: str
    adresse: str
    code_postal: str
    commune: str
    code_insee: str
    regime_icpe: str
    categorie_seveso: str
    priorite_nationale: bool
    directive_ied: bool
    stored_lat: float | None
    stored_lon: float | None
    geocoded_lat: float | None
    geocoded_lon: float | None
    geocoded_label: str
    geocoded_score: float | None
    geocoded_type: str
    reverse_label: str
    forward_distance_m: float | None
    audit_class: AuditClass
    stored_in_reserve: str
    geocoded_in_reserve: str
    stored_reserve_distance_m: float | None
    geocoded_reserve_distance_m: float | None
    reserve_ambiguous: bool
    reserve_boundary_proximity: bool
    url_fiche_georisques: str


# Default thresholds (CLI-overridable)
DEFAULT_THRESHOLDS: Thresholds = {
    "minor_m": 25.0,
    "suspicious_m": 100.0,
    "severe_m": 500.0,
    "very_severe_m": 2000.0,
    "score_cutoff": 0.4,
}


# --- Math primitives ----------------------------------------------------

EARTH_RADIUS_M = 6_371_000.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    """Ray-casting PIP for a single linear ring (list of [lon, lat] pairs).

    Standard "is point inside the polygon defined by this ring" check.
    Returns True for points strictly inside, False otherwise. Points
    exactly on the boundary may go either way (acceptable for our use:
    we test commune membership, not parcel boundaries).
    """
    x, y = point
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def point_in_polygon(point: tuple[float, float], polygon: list[list[list[float]]]) -> bool:
    """PIP for a Polygon (outer ring + optional holes)."""
    if not polygon:
        return False
    if not point_in_ring(point, polygon[0]):
        return False
    for hole in polygon[1:]:
        if point_in_ring(point, hole):
            return False
    return True


def point_in_multipolygon(
    point: tuple[float, float],
    multipoly: list[list[list[list[float]]]],
) -> bool:
    """PIP for a MultiPolygon (list of polygons)."""
    return any(point_in_polygon(point, poly) for poly in multipoly)


def distance_point_to_segment_m(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """Approximate distance in metres from point P to segment AB on a
    locally flat WGS84 plane. Good enough for distances < a few km."""
    plat, plon = p
    alat, alon = a
    blat, blon = b
    # Convert to local meters using a flat-earth approximation centered at A
    cos_lat = math.cos(math.radians(alat))
    ax, ay = 0.0, 0.0
    bx = (blon - alon) * 111_320.0 * cos_lat
    by = (blat - alat) * 111_320.0
    px = (plon - alon) * 111_320.0 * cos_lat
    py = (plat - alat) * 111_320.0
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-9:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def distance_to_polygon_boundary_m(
    point: tuple[float, float],
    polygon: list[list[list[float]]],
) -> float:
    """Minimum distance from point to any edge of any ring in the polygon."""
    best = float("inf")
    for ring in polygon:
        n = len(ring)
        for i in range(n):
            a = (ring[i][1], ring[i][0])  # (lat, lon)
            b = (ring[(i + 1) % n][1], ring[(i + 1) % n][0])
            d = distance_point_to_segment_m(point, a, b)
            if d < best:
                best = d
    return best


def polygon_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """Approximate centroid of a polygon's outer ring (mean of vertices)."""
    if not ring:
        return (0.0, 0.0)
    lats = [pt[1] for pt in ring]
    lons = [pt[0] for pt in ring]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


# --- Geometry loading ---------------------------------------------------

def load_geojson(path: Path) -> list[dict]:
    """Read a GeoJSON FeatureCollection. Returns the features list."""
    with path.open(encoding="utf-8") as h:
        data = json.load(h)
    return data.get("features") or []


def extract_polygons(geometry: dict | None) -> list[list[list[list[float]]]]:
    """Normalize a GeoJSON geometry to a list of polygons.

    Polygon → [polygon]
    MultiPolygon → list of polygons
    Other types → []
    """
    if not geometry:
        return []
    t = geometry.get("type")
    coords = geometry.get("coordinates")
    if t == "Polygon" and coords:
        return [coords]
    if t == "MultiPolygon" and coords:
        return list(coords)
    return []


def load_commune_polygons() -> dict[str, list[list[list[list[float]]]]]:
    """Returns dict[insee_code → list of polygons] from gironde-communes.geojson."""
    features = load_geojson(CARTE_DATA_DIR / "gironde-communes.geojson")
    out: dict[str, list[list[list[list[float]]]]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        insee = props.get("insee")
        if not insee:
            continue
        polys = extract_polygons(feat.get("geometry"))
        if polys:
            out[insee] = polys
    return out


def load_gironde_contour() -> list[list[list[list[float]]]]:
    """Returns the département contour as a list of polygons (single MultiPolygon)."""
    features = load_geojson(CARTE_DATA_DIR / "gironde-contour.geojson")
    if not features:
        return []
    return extract_polygons(features[0].get("geometry"))


def load_reserve_polygons() -> list[tuple[str, list[list[list[list[float]]]]]]:
    """Returns list of (reserve_name, polygons) for RNN + RNR.

    RNN file currently has 9 features in Gironde; RNR file has 0
    (no RNR in the département). Both are loaded the same way.
    """
    out: list[tuple[str, list[list[list[list[float]]]]]] = []
    for filename in ("reserves-naturelles-nationales.geojson", "reserves-naturelles-regionales.geojson"):
        path = CARTE_DATA_DIR / filename
        if not path.exists():
            continue
        for feat in load_geojson(path):
            props = feat.get("properties") or {}
            name = props.get("nom") or props.get("id_mnhn") or "?"
            polys = extract_polygons(feat.get("geometry"))
            if polys:
                out.append((name, polys))
    return out


# --- Data loading -------------------------------------------------------

def load_bulk_enriched() -> list[AuditedRow]:
    """Read InstallationClassee_enrichi.csv (delimiter=';') with column validation.

    Returns rows typed as ``AuditedRow`` — safe because ``AuditedRow`` is
    ``total=False`` (all fields ``NotRequired``), so a CSV row carrying
    only the bulk columns is a structurally valid ``AuditedRow``. The
    audit passes populate the remaining fields in place.

    The ``cast`` lives here — at the I/O boundary — because parsing
    external data into a typed shape is exactly what ``cast`` is for.
    """
    with DONNEES_BULK_ENRICHI_CSV.open(encoding="utf-8", newline="") as h:
        reader = csv.DictReader(h, delimiter=";")
        require_columns(reader.fieldnames, BULK_ENRICHED_REQUIRED_COLUMNS, DONNEES_BULK_ENRICHI_CSV)
        return cast("list[AuditedRow]", list(reader))


def parse_float(value: str) -> float | None:
    """Stringly-typed CSV value → float | None. Empty/invalid → None.

    Accepts both ``.`` and ``,`` as the decimal separator. Géorisques
    bulk exports are dot-formatted today, but data.gouv.fr derivatives
    occasionally ship French-locale floats — without this fallback,
    a row with ``"44,8"`` would silently become ``None`` and route to
    the null-island bucket.

    The comma → dot substitution is rejected if the input contains
    multiple commas (looks like a thousands separator) so we never
    silently turn ``"1,234"`` into ``1.234`` when the source meant
    1234.0.
    """
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        if value.count(",") == 1 and "." not in value:
            try:
                return float(value.replace(",", "."))
            except ValueError:
                return None
        return None


# --- Signal pass 1: sentinels -------------------------------------------

def pass_1_sentinels(
    rows: list[AuditedRow],
    contour_multipoly: list[list[list[list[float]]]],
    commune_polys: dict[str, list[list[list[list[float]]]]],
) -> None:
    """Tag rows with cheap offline sentinels in the `sentinel_flags` column.

    - null_island: lat or lon is exactly 0
    - outside_gironde: stored point is not inside the département contour
    - commune_centroid: stored point is within 50m of its declared
      commune's centroid (classic geocoder-fallback tell)
    - duplicate_coords: 3+ sites share the same rounded (lat, lon)
    """
    # Pre-pass to count duplicate coordinates
    coord_counts: dict[tuple[float, float], int] = {}
    for row in rows:
        lat = parse_float(row.get("latitude", ""))
        lon = parse_float(row.get("longitude", ""))
        if lat is not None and lon is not None:
            key = (round(lat, 5), round(lon, 5))
            coord_counts[key] = coord_counts.get(key, 0) + 1

    for row in rows:
        flags: list[str] = []
        lat = parse_float(row.get("latitude", ""))
        lon = parse_float(row.get("longitude", ""))
        row["stored_lat"] = lat
        row["stored_lon"] = lon

        # Null island = the point (0, 0), or missing coords. The previous
        # check `lat == 0 or lon == 0` over-fired on legitimate sites with
        # exactly one zero component — eastern Gironde near Blaye sits on
        # the prime meridian (lon ≈ 0.0), and any site there was being
        # misclassified and skipped by BAN.
        if lat is None or lon is None or (lat == 0 and lon == 0):
            flags.append("null_island")
        else:
            # outside_gironde test against the département contour
            if contour_multipoly and not point_in_multipolygon((lon, lat), contour_multipoly):
                flags.append("outside_gironde")

            # commune_centroid test
            insee = row.get("codeInsee", "")
            if insee in commune_polys:
                # use first ring of first polygon as the centroid source
                poly = commune_polys[insee]
                if poly and poly[0] and poly[0][0]:
                    centroid_lat, centroid_lon = polygon_centroid(poly[0][0])
                    d = haversine(lat, lon, centroid_lat, centroid_lon)
                    if d < COMMUNE_CENTROID_RADIUS_M:
                        flags.append("commune_centroid")

            # duplicate_coords test
            key = (round(lat, 5), round(lon, 5))
            if coord_counts.get(key, 0) >= 3:
                flags.append("duplicate_coords")

        row["sentinel_flags"] = ",".join(flags)


# --- Signal pass 2: commune PIP -----------------------------------------

def pass_2_commune_pip(
    rows: list[AuditedRow],
    commune_polys: dict[str, list[list[list[list[float]]]]],
) -> None:
    """Tristate PIP: True if the stored point is in the declared commune,
    False if it's in Gironde but the wrong commune, None if the commune
    polygon is unknown (DD #10 — honest 'we couldn't check')."""
    for row in rows:
        lat = row.get("stored_lat")
        lon = row.get("stored_lon")
        insee = row.get("codeInsee", "")
        if lat is None or lon is None:
            row["pip_in_commune"] = None
            continue
        if insee not in commune_polys:
            row["pip_in_commune"] = None
            continue
        polys = commune_polys[insee]
        is_inside = any(point_in_polygon((lon, lat), poly) for poly in polys)
        row["pip_in_commune"] = is_inside


# --- BAN HTTP plumbing --------------------------------------------------

def post_with_retry(url: str, files: dict[str, object], attempts: int = 3) -> bytes:
    """POST multipart/form-data with exponential backoff. Lazy import of
    requests so the test loader can import this module without it (DD #49).

    Per-attempt failures are logged to stderr so the student running the
    pipeline sees retry progress instead of a 14-second silent hang on
    the default 3 attempts at 2/4/8 s.
    """
    import requests  # noqa: PLC0415

    delay = 2.0
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            r = requests.post(url, files=files, timeout=120)
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            last_err = exc
            if attempt < attempts - 1:
                print(
                    f"[ban] retry {attempt + 1}/{attempts} after {exc} "
                    f"(waiting {delay:.0f}s)…",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay *= 2
    # Use an explicit RuntimeError instead of `assert last_err is not None`
    # because asserts are stripped under `python -O`, which would degrade
    # the failure mode to a confusing `raise None` → TypeError.
    if last_err is None:
        raise RuntimeError(
            "post_with_retry: retry loop exited without capturing an exception"
        )
    raise last_err


def parse_ban_response(csv_bytes: bytes) -> list[dict[str, str]]:
    """Parse BAN's CSV response into a list of dicts. Pure function, testable."""
    text = csv_bytes.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_input_csv(csv_text: str) -> str:
    return hashlib.sha256(csv_text.encode("utf-8")).hexdigest()


# --- Signal pass 3: BAN forward -----------------------------------------

# --- BAN forward cascade (DD-fix from verification of empty-address pattern)
#
# The naïve approach (concatenate adresse1+adresse2+adresse3 with spaces)
# produces strings like "Relais de Pichey 127 avenue de l'Yser" that BAN
# can't match because the company-name prefix breaks the geocoder. Live
# audit run found 906/2890 (31%) failures with this strategy, including
# 142 addresses that started with a digit and looked perfectly geocodable.
#
# The cascade tries three address-building strategies in order, taking
# the first BAN success per row. Each strategy has its own on-disk cache
# so re-runs are fast and incremental (changing only the strategy 1 input
# only invalidates strategies 2 and 3 if their input transitively changes).
#
# Strategy 1 (adresse1):    Most addresses have the street in adresse1.
#                            Verified live: this alone matches the simple
#                            "VALAD PARC DE BRUGES Rue de Milan" case.
# Strategy 2 (adresse2):    Some sites put the street in adresse2 because
#                            adresse1 is "Château X" or "Centre Commercial Y".
# Strategy 3 (combined):    Last resort: adresse1 + adresse2 joined.
#                            Same as the original buggy approach but only
#                            for rows that failed both individual attempts.

ADDRESS_STRATEGIES: list[tuple[str, str]] = [
    ("adresse1", "adresse1"),
    ("adresse2", "adresse2"),
    ("combined", "adresse1+adresse2"),
]


def _build_address_for_strategy(row: AuditedRow, strategy: str) -> str:
    """Build the address string BAN should geocode for a given strategy."""
    if strategy == "adresse1":
        return row.get("adresse1", "").strip()
    if strategy == "adresse2":
        return row.get("adresse2", "").strip()
    if strategy == "combined":
        parts = [
            row.get("adresse1", "").strip(),
            row.get("adresse2", "").strip(),
        ]
        return " ".join(p for p in parts if p)
    raise ValueError(f"unknown strategy: {strategy}")


def pass_3_ban_forward(rows: list[AuditedRow]) -> None:
    """Cascade BAN forward geocoding through the 3 address strategies.
    First success per row wins. Cached on disk per strategy."""
    DONNEES_AUDIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    pending_ids: set[str] = {r.get("id_icpe", "") for r in rows if r.get("id_icpe", "")}
    successes: dict[str, dict[str, str]] = {}

    for strategy_id, strategy_label in ADDRESS_STRATEGIES:
        if not pending_ids:
            break

        # Build the input subset for this strategy: pending rows where the
        # strategy yields a non-empty address.
        attempt_rows: list[AuditedRow] = []
        for row in rows:
            rid = row.get("id_icpe", "")
            if rid not in pending_ids:
                continue
            if _build_address_for_strategy(row, strategy_id):
                attempt_rows.append(row)

        if not attempt_rows:
            print(
                f"[ban-forward:{strategy_label}] no eligible rows "
                f"(remaining pending: {len(pending_ids)})"
            )
            continue

        new_successes = _ban_forward_attempt(attempt_rows, strategy_id, strategy_label)
        for k, v in new_successes.items():
            successes[k] = v
            pending_ids.discard(k)

        print(
            f"[ban-forward:{strategy_label}] {len(attempt_rows)} attempts → "
            f"{len(new_successes)} new successes, {len(pending_ids)} still pending"
        )

    print(
        f"[ban-forward] cascade summary: {len(successes)} resolved, "
        f"{len(pending_ids)} unresolved across {len(ADDRESS_STRATEGIES)} strategies"
    )

    _apply_ban_forward(rows, successes)


def _ban_forward_attempt(
    attempt_rows: list[AuditedRow],
    strategy_id: str,
    strategy_label: str,
) -> dict[str, dict[str, str]]:
    """Single BAN POST for one cascade strategy. Returns dict of successful
    BAN response rows keyed by id_icpe. Caches on disk per strategy."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id_icpe", "adresse", "postcode", "citycode"])
    writer.writeheader()
    for row in attempt_rows:
        writer.writerow({
            "id_icpe": row.get("id_icpe", ""),
            "adresse": _build_address_for_strategy(row, strategy_id),
            "postcode": row.get("codePostal", ""),
            "citycode": row.get("codeInsee", ""),
        })
    input_csv = buf.getvalue()
    input_hash = hash_input_csv(input_csv)

    cache_path = DONNEES_AUDIT_CACHE_DIR / f"ban-forward-{strategy_id}.csv"
    cache_meta = cache_path.with_suffix(".csv.meta")

    if cache_path.exists() and cache_meta.exists():
        meta = cache_meta.read_text(encoding="utf-8").strip()
        if meta == input_hash:
            print(
                f"[ban-forward:{strategy_label}] cache hit "
                f"({cache_path.relative_to(PROJECT_ROOT)})"
            )
            with cache_path.open("rb") as f:
                response_bytes = f.read()
            return _extract_ok_responses(response_bytes)

    print(
        f"[ban-forward:{strategy_label}] cache miss — POST "
        f"{len(attempt_rows)} rows to BAN…"
    )
    response_bytes = post_with_retry(
        BAN_FORWARD_URL,
        files={
            "data": ("input.csv", input_csv, "text/csv"),
            "columns": (None, "adresse"),
            "postcode": (None, "postcode"),
            "citycode": (None, "citycode"),
        },
    )
    print(f"[ban-forward:{strategy_label}] received {len(response_bytes)} bytes")

    # Use atomic_write's default encoding="utf-8". The previous
    # encoding=None argument was both a type error against the
    # `encoding: str` signature and a portability bug — on a non-UTF-8
    # locale (Windows CP1252) it would write the cache file with the
    # wrong codec, causing a downstream UnicodeDecodeError on re-read.
    with atomic_write(cache_path) as f:
        f.write(response_bytes.decode("utf-8"))
    cache_meta.write_text(input_hash, encoding="utf-8")
    print(
        f"[ban-forward:{strategy_label}] cached → "
        f"{cache_path.relative_to(PROJECT_ROOT)}"
    )

    return _extract_ok_responses(response_bytes)


def _extract_ok_responses(response_bytes: bytes) -> dict[str, dict[str, str]]:
    """Parse BAN response, return only the rows where result_status='ok'."""
    parsed = parse_ban_response(response_bytes)
    out: dict[str, dict[str, str]] = {}
    for entry in parsed:
        key = entry.get("id_icpe", "")
        status = (entry.get("result_status") or "").strip()
        if key and status == "ok":
            out[key] = entry
    return out


def _apply_ban_forward(
    rows: list[AuditedRow],
    successes: dict[str, dict[str, str]],
) -> None:
    """Apply per-row BAN forward successes to the audit rows. Rows without
    a successful BAN match across all cascade strategies get
    forward_error='ban_no_match_after_cascade'."""
    for row in rows:
        key = row.get("id_icpe", "")
        ban = successes.get(key)
        if not ban:
            row["forward_lat"] = None
            row["forward_lon"] = None
            row["forward_score"] = None
            row["forward_type"] = None
            row["forward_label"] = None
            row["forward_citycode"] = None
            row["forward_distance_m"] = None
            row["forward_error"] = "ban_no_match_after_cascade"
            continue

        # BAN response columns: longitude, latitude (lowercase, no result_ prefix)
        lon = parse_float(ban.get("longitude", ""))
        lat = parse_float(ban.get("latitude", ""))
        score = parse_float(ban.get("result_score", ""))

        row["forward_lat"] = lat
        row["forward_lon"] = lon
        row["forward_score"] = score
        row["forward_type"] = ban.get("result_type") or None
        row["forward_label"] = ban.get("result_label") or None
        row["forward_citycode"] = ban.get("result_citycode") or None
        row["forward_error"] = ""

        stored_lat = row.get("stored_lat")
        stored_lon = row.get("stored_lon")
        if (
            lat is not None
            and lon is not None
            and stored_lat is not None
            and stored_lon is not None
        ):
            row["forward_distance_m"] = haversine(stored_lat, stored_lon, lat, lon)
        else:
            row["forward_distance_m"] = None


# --- Signal pass 3b: OpenCage fallback ----------------------------------

def _opencage_query(query: str, api_key: str) -> dict | None:
    """Single OpenCage forward request. Returns the first result, or None."""
    import requests  # noqa: PLC0415
    params = {
        "q": query,
        "key": api_key,
        "countrycode": "fr",
        "limit": 1,
        "no_annotations": 1,
        "language": "fr",
    }
    r = requests.get(OPENCAGE_URL, params=params, timeout=15)
    # OpenCage returns 402 when over the daily quota, 401 if key is bad,
    # 429 if over the per-second rate. Bubble up so the caller can stop.
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    return results[0]


def _apply_opencage_success(row: AuditedRow, entry: dict) -> None:
    """Update row's forward_* fields from an OpenCage result.

    Guards against missing or null geometry: country-level matches and
    other low-confidence results can come back without ``geometry``,
    and the previous fallback ``geom.get("lat", 0)`` would write
    (0, 0) into the row, then the downstream haversine would fabricate
    a multi-thousand-km distance and misclassify the site as
    ``very_severe``.
    """
    geom = entry.get("geometry") or {}
    if "lat" not in geom or "lng" not in geom:
        return
    try:
        lat = float(geom["lat"])
        lon = float(geom["lng"])
    except (ValueError, TypeError):
        return
    if lat == 0 and lon == 0:
        # Genuine null-island result — treat as no match.
        return

    # OpenCage's `confidence` is 1-10 (10 = building level, 1 = country
    # level). Map to a 0-1 forward_score that the BAN ladder understands.
    confidence = entry.get("confidence", 5)
    try:
        score = max(0.0, min(1.0, float(confidence) / 10.0))
    except (ValueError, TypeError):
        score = 0.5

    # Map OpenCage's component types to BAN-style forward_type values
    components = entry.get("components") or {}
    if components.get("house_number"):
        forward_type = "housenumber"
    elif components.get("road") or components.get("street"):
        forward_type = "street"
    elif (
        components.get("hamlet")
        or components.get("village")
        or components.get("suburb")
        or components.get("neighbourhood")
    ):
        forward_type = "locality"
    elif components.get("city") or components.get("town"):
        forward_type = "municipality"
    else:
        forward_type = "locality"

    label = (entry.get("formatted") or "")[:200]

    row["forward_lat"] = lat
    row["forward_lon"] = lon
    row["forward_score"] = score
    row["forward_type"] = forward_type
    row["forward_label"] = "[OPENCAGE] " + label
    row["forward_citycode"] = None  # OpenCage doesn't return INSEE codes
    row["forward_error"] = ""

    stored_lat = row.get("stored_lat")
    stored_lon = row.get("stored_lon")
    if stored_lat is not None and stored_lon is not None:
        row["forward_distance_m"] = haversine(stored_lat, stored_lon, lat, lon)
    else:
        row["forward_distance_m"] = None


def pass_3b_opencage_fallback(rows: list[AuditedRow]) -> None:
    """4th cascade strategy: OpenCage geocoder.

    Aggregates OSM, GeoNames, and other sources. Free tier explicitly
    allows bulk geocoding (2500 req/day, 1 req/sec). Requires the
    OPENCAGE_API_KEY environment variable; the function silently skips
    if absent so the audit still works without it.
    """
    api_key = os.environ.get(OPENCAGE_API_KEY_ENV)
    if not api_key:
        print(
            f"[opencage] {OPENCAGE_API_KEY_ENV} not set, skipping (BAN cascade only)",
            file=sys.stderr,
        )
        return

    DONNEES_AUDIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    pending = [r for r in rows if r.get("forward_error")]
    if not pending:
        print("[opencage] no rows pending after BAN cascade — skipping")
        return

    print(f"[opencage] {len(pending)} rows pending after BAN cascade")

    # Load on-disk cache (cache key is just the query, no API key)
    cache: dict[str, dict | None] = {}
    if OPENCAGE_CACHE.exists():
        try:
            with OPENCAGE_CACHE.open(encoding="utf-8") as f:
                cache = json.load(f)
            print(f"[opencage] cache: {len(cache)} entries on disk")
        except json.JSONDecodeError:
            print("[opencage] cache file corrupted, starting fresh", file=sys.stderr)
            cache = {}

    new_successes = 0
    api_calls = 0
    cache_hits = 0
    last_call_at = 0.0
    quota_exhausted = False

    for row in pending:
        rid = row.get("id_icpe", "")
        if not rid:
            continue

        addr_parts = [
            row.get("adresse1", "").strip(),
            row.get("commune", "").strip(),
            row.get("codePostal", "").strip(),
        ]
        addr_parts = [p for p in addr_parts if p]
        if not addr_parts:
            continue
        query = ", ".join(addr_parts) + ", France"
        cache_key = f"{rid}:{query}"

        if cache_key in cache:
            entry = cache[cache_key]
            cache_hits += 1
            if entry:
                _apply_opencage_success(row, entry)
                new_successes += 1
            continue

        if quota_exhausted:
            continue  # don't try further OpenCage calls today

        # Rate limit
        elapsed = time.time() - last_call_at
        if elapsed < OPENCAGE_RATE_LIMIT_SEC:
            time.sleep(OPENCAGE_RATE_LIMIT_SEC - elapsed)

        transient_failure = False
        try:
            entry = _opencage_query(query, api_key)
            api_calls += 1
            last_call_at = time.time()
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            # OpenCage uses 402 for daily quota exhaustion (permanent until
            # tomorrow) and 429 for per-second rate-limit (transient).
            # `requests.HTTPError.__str__` produces strings like
            # "402 Client Error: Payment Required …" / "429 Client Error:
            # Too Many Requests …", so substring matching on the status
            # code is the contract here.
            if "402" in err or "quota" in err.lower():
                print(
                    f"[opencage] quota exhausted ({err}). "
                    f"Stopping OpenCage cascade. Cached {api_calls} responses.",
                    file=sys.stderr,
                )
                quota_exhausted = True
                last_call_at = time.time()
                continue
            if "429" in err:
                # Transient burst limit. Skip this row WITHOUT caching
                # the null — re-running should retry it. Sleep extra to
                # let the per-second window roll over.
                print(
                    f"[opencage] burst-limit (429) for {rid} ({query[:60]}); "
                    f"skipping without caching null",
                    file=sys.stderr,
                )
                transient_failure = True
                last_call_at = time.time()
                time.sleep(OPENCAGE_RATE_LIMIT_SEC)
            else:
                print(
                    f"[opencage] error for {rid} ({query[:60]}): {exc}",
                    file=sys.stderr,
                )
                entry = None
                last_call_at = time.time()

        if transient_failure:
            continue
        cache[cache_key] = entry  # cache nulls to avoid retrying
        if entry:
            _apply_opencage_success(row, entry)
            new_successes += 1

        # Periodic cache flush
        if api_calls > 0 and api_calls % 50 == 0:
            _save_opencage_cache(cache)
            print(
                f"[opencage] progress: {api_calls} API calls, "
                f"{new_successes} successes, {cache_hits} cache hits"
            )

    _save_opencage_cache(cache)
    print(
        f"[opencage] done: {api_calls} API calls, {cache_hits} cache hits, "
        f"{new_successes} new successes (out of {len(pending)} pending)"
    )


def _save_opencage_cache(cache: dict) -> None:
    with atomic_write(OPENCAGE_CACHE) as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# --- Weak-match retry: send commune-level OpenCage matches to Nominatim --

# Marker put in forward_error to flag rows being retried
_RETRY_MARKER = "opencage_weak_pending_nominatim_retry"


class OpencageSnapshot(TypedDict):
    """Snapshot of an OpenCage forward-geocode result, captured before
    the row is re-queued for the Nominatim retry pass. Restored verbatim
    by ``restore_opencage_if_nominatim_didnt_improve`` if Nominatim
    didn't return a more specific match.
    """
    forward_lat: float | None
    forward_lon: float | None
    forward_score: float | None
    forward_type: str | None
    forward_label: str | None
    forward_citycode: str | None
    forward_distance_m: float | None


# Stash type alias: id_icpe → OpencageSnapshot to restore later.
OpencageStash = dict[str, OpencageSnapshot]


def mark_weak_opencage_for_retry(rows: list[AuditedRow]) -> OpencageStash:
    """OpenCage successes are often just commune centroids (forward_type
    in {locality, municipality}). Re-mark those as pending so the
    Nominatim cascade gets a shot at finding something more specific.

    Returns the stash of OpenCage values that should be restored if
    Nominatim doesn't improve on them. Pass the returned stash to
    ``restore_opencage_if_nominatim_didnt_improve``.

    The stash is returned (not stored in module-level state) so the two
    functions are independently testable and re-entrant — a bug fix for
    the previous implicit-state-channel pattern flagged by py-arch.
    """
    stash: OpencageStash = {}
    for row in rows:
        label = row.get("forward_label") or ""
        ftype = row.get("forward_type") or ""
        if not label.startswith("[OPENCAGE]"):
            continue
        if ftype not in ("locality", "municipality"):
            continue
        rid = row.get("id_icpe", "")
        if not rid:
            continue
        stash[rid] = OpencageSnapshot(
            forward_lat=row.get("forward_lat"),
            forward_lon=row.get("forward_lon"),
            forward_score=row.get("forward_score"),
            forward_type=row.get("forward_type"),
            forward_label=row.get("forward_label"),
            forward_citycode=row.get("forward_citycode"),
            forward_distance_m=row.get("forward_distance_m"),
        )
        row["forward_error"] = _RETRY_MARKER
    if stash:
        print(
            f"[opencage→nominatim] {len(stash)} weak OpenCage matches "
            f"(commune-level) re-queued for Nominatim retry"
        )
    return stash


def restore_opencage_if_nominatim_didnt_improve(
    rows: list[AuditedRow], stash: OpencageStash
) -> None:
    """After Nominatim ran on the retry queue, decide per-row whether
    Nominatim's result is more specific than the stashed OpenCage match.

    If Nominatim returned housenumber/street → keep Nominatim's result.
    Otherwise (still locality/municipality, or Nominatim didn't process
    the row) → restore the OpenCage commune-level fallback.

    The stash is now an explicit parameter (was previously read from a
    module-level dict).
    """
    nominatim_specific = 0
    nominatim_also_weak = 0
    not_processed = 0

    def _restore_snapshot(target: AuditedRow, src: OpencageSnapshot) -> None:
        # Spelled out so each TypedDict key is a literal — no
        # `# type: ignore` needed. The OpencageSnapshot TypedDict
        # carries the same per-field types as AuditedRow's forward_*
        # columns, so each assignment is type-safe end to end.
        target["forward_lat"] = src["forward_lat"]
        target["forward_lon"] = src["forward_lon"]
        target["forward_score"] = src["forward_score"]
        target["forward_type"] = src["forward_type"]
        target["forward_label"] = src["forward_label"]
        target["forward_citycode"] = src["forward_citycode"]
        target["forward_distance_m"] = src["forward_distance_m"]

    for row in rows:
        rid = row.get("id_icpe", "")
        if rid not in stash:
            continue
        snapshot = stash[rid]

        # Case 1: forward_error still has the retry marker → Nominatim didn't
        # process it (rate limited, error, no query buildable). Restore.
        if row.get("forward_error") == _RETRY_MARKER:
            _restore_snapshot(row, snapshot)
            row["forward_error"] = ""
            not_processed += 1
            continue

        # Case 2: Nominatim cleared the error → it returned something. Check
        # whether it's more specific than the OpenCage commune-level match.
        new_type = row.get("forward_type") or ""
        if new_type in ("housenumber", "street"):
            nominatim_specific += 1
            # Keep Nominatim's result.
        else:
            # Nominatim also returned locality/municipality — no improvement.
            # Restore OpenCage so the reviewer at least sees the OpenCage label.
            _restore_snapshot(row, snapshot)
            row["forward_error"] = ""
            nominatim_also_weak += 1

    if (nominatim_specific or nominatim_also_weak or not_processed) > 0:
        print(
            f"[opencage→nominatim] retry results: "
            f"{nominatim_specific} improved by Nominatim, "
            f"{nominatim_also_weak} both still weak (kept OpenCage), "
            f"{not_processed} not processed"
        )


# --- Signal pass 3c: Nominatim last-resort fallback ---------------------

# Nominatim type → BAN-style result_type mapping
_NOMINATIM_TYPE_MAP = {
    "house": "housenumber",
    "building": "housenumber",
    "street": "street",
    "road": "street",
    "residential": "street",
    "village": "locality",
    "town": "locality",
    "hamlet": "locality",
    "suburb": "locality",
    "neighbourhood": "locality",
    "city": "municipality",
    "administrative": "municipality",
}


def _nominatim_query(query: str) -> dict | None:
    """Single Nominatim search request. Returns the first result, or None."""
    import requests  # noqa: PLC0415
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "fr",
        "addressdetails": 1,
    }
    headers = {"User-Agent": NOMINATIM_USER_AGENT, "Accept-Language": "fr"}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]


def _apply_nominatim_success(row: AuditedRow, entry: dict) -> None:
    """Update row's forward_* fields from a Nominatim search result."""
    try:
        lat = float(entry.get("lat", "0"))
        lon = float(entry.get("lon", "0"))
    except (ValueError, TypeError):
        return

    nominatim_type = entry.get("type", "") or ""
    mapped_type = _NOMINATIM_TYPE_MAP.get(nominatim_type, "locality")

    # Importance is Nominatim's 0-1 confidence proxy. Map to forward_score.
    importance = entry.get("importance")
    try:
        score = float(importance) if importance is not None else 0.5
    except (ValueError, TypeError):
        score = 0.5

    # Tag the label so the reviewer knows the source. The audit tool's
    # review UI can render this prefix as a small badge.
    label = (entry.get("display_name") or "")[:200]

    row["forward_lat"] = lat
    row["forward_lon"] = lon
    row["forward_score"] = score
    row["forward_type"] = mapped_type
    row["forward_label"] = "[OSM] " + label
    # Nominatim's address details may include a postcode but no INSEE
    # citycode (the Code Officiel Géographique is INSEE-specific). Leave
    # forward_citycode empty so the wrong_commune check doesn't fire on
    # absent data.
    row["forward_citycode"] = None
    row["forward_error"] = ""  # clear the BAN error

    stored_lat = row.get("stored_lat")
    stored_lon = row.get("stored_lon")
    if stored_lat is not None and stored_lon is not None:
        row["forward_distance_m"] = haversine(stored_lat, stored_lon, lat, lon)
    else:
        row["forward_distance_m"] = None


def pass_3c_nominatim_fallback(rows: list[AuditedRow]) -> None:
    """4th cascade strategy: query Nominatim (OSM) for any row where BAN
    failed across all 3 BAN strategies.

    Nominatim has different data than BAN — OSM contributors index
    lieu-dit names, château names, and vintage industrial addresses
    that BAN's authoritative French base doesn't have. This recovers
    a meaningful fraction of the BAN-unresolved set.

    Rate-limited to 1 req/sec per Nominatim's usage policy. The first
    run takes ~12 minutes for 700 pending rows; subsequent runs are
    instant from the JSON cache. Each query is cached individually so
    cache invalidation is per-row, not per-batch.
    """
    DONNEES_AUDIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    pending = [r for r in rows if r.get("forward_error")]
    if not pending:
        print("[nominatim] no rows pending after BAN cascade — skipping")
        return

    print(f"[nominatim] {len(pending)} rows pending after BAN cascade")

    # Load on-disk cache
    cache: dict[str, dict | None] = {}
    if NOMINATIM_CACHE.exists():
        try:
            with NOMINATIM_CACHE.open(encoding="utf-8") as f:
                cache = json.load(f)
            print(f"[nominatim] cache: {len(cache)} entries on disk")
        except json.JSONDecodeError:
            print("[nominatim] cache file corrupted, starting fresh", file=sys.stderr)
            cache = {}

    new_successes = 0
    api_calls = 0
    cache_hits = 0
    last_call_at = 0.0

    for row in pending:
        rid = row.get("id_icpe", "")
        if not rid:
            continue

        # Build query: prefer adresse1 + commune + postcode. Skip rows
        # where there's nothing useful to query (empty addr + nothing else).
        addr_parts = [
            row.get("adresse1", "").strip(),
            row.get("commune", "").strip(),
            row.get("codePostal", "").strip(),
        ]
        addr_parts = [p for p in addr_parts if p]
        if not addr_parts:
            continue
        query = ", ".join(addr_parts) + ", France"
        cache_key = f"{rid}:{query}"

        if cache_key in cache:
            entry = cache[cache_key]
            cache_hits += 1
            if entry:
                _apply_nominatim_success(row, entry)
                new_successes += 1
            continue

        # Rate limit
        elapsed = time.time() - last_call_at
        if elapsed < NOMINATIM_RATE_LIMIT_SEC:
            time.sleep(NOMINATIM_RATE_LIMIT_SEC - elapsed)

        try:
            entry = _nominatim_query(query)
            api_calls += 1
            last_call_at = time.time()
        except Exception as exc:  # noqa: BLE001
            print(f"[nominatim] error for {rid} ({query[:60]}): {exc}", file=sys.stderr)
            entry = None
            last_call_at = time.time()  # rate limit even on errors

        # Cache even nulls so we don't retry next run
        cache[cache_key] = entry
        if entry:
            _apply_nominatim_success(row, entry)
            new_successes += 1

        # Periodic cache flush so partial progress survives a crash
        if api_calls > 0 and api_calls % 50 == 0:
            _save_nominatim_cache(cache)
            print(
                f"[nominatim] progress: {api_calls} API calls, "
                f"{new_successes} successes, {cache_hits} cache hits"
            )

    _save_nominatim_cache(cache)
    print(
        f"[nominatim] done: {api_calls} API calls, {cache_hits} cache hits, "
        f"{new_successes} new successes (out of {len(pending)} pending)"
    )


def _save_nominatim_cache(cache: dict) -> None:
    with atomic_write(NOMINATIM_CACHE) as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# --- Signal pass 4: BAN reverse -----------------------------------------

def pass_4_ban_reverse(rows: list[AuditedRow]) -> None:
    """Reverse-geocode each row's stored coordinates via BAN /reverse/csv/.
    Same caching pattern as pass_3."""
    DONNEES_AUDIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id_icpe", "lat", "lon"])
    writer.writeheader()
    for row in rows:
        lat = row.get("stored_lat")
        lon = row.get("stored_lon")
        writer.writerow({
            "id_icpe": row.get("id_icpe", ""),
            "lat": str(lat) if lat is not None else "",
            "lon": str(lon) if lon is not None else "",
        })
    input_csv = buf.getvalue()
    input_hash = hash_input_csv(input_csv)

    cache_path = BAN_CACHE_REVERSE
    cache_meta = cache_path.with_suffix(".csv.meta")
    if cache_path.exists() and cache_meta.exists():
        meta = cache_meta.read_text(encoding="utf-8").strip()
        if meta == input_hash:
            print(f"[ban-reverse] cache hit ({cache_path.relative_to(PROJECT_ROOT)})")
            with cache_path.open("rb") as f:
                response_bytes = f.read()
            _apply_ban_reverse(rows, response_bytes)
            return

    print(f"[ban-reverse] cache miss — POST {len(rows)} rows to BAN…")
    response_bytes = post_with_retry(
        BAN_REVERSE_URL,
        files={
            "data": ("input.csv", input_csv, "text/csv"),
        },
    )
    print(f"[ban-reverse] received {len(response_bytes)} bytes")

    # Use atomic_write's default encoding="utf-8". The previous
    # encoding=None argument was both a type error against the
    # `encoding: str` signature and a portability bug — on a non-UTF-8
    # locale (Windows CP1252) it would write the cache file with the
    # wrong codec, causing a downstream UnicodeDecodeError on re-read.
    with atomic_write(cache_path) as f:
        f.write(response_bytes.decode("utf-8"))
    cache_meta.write_text(input_hash, encoding="utf-8")
    print(f"[ban-reverse] cached → {cache_path.relative_to(PROJECT_ROOT)}")

    _apply_ban_reverse(rows, response_bytes)


def _apply_ban_reverse(rows: list[AuditedRow], response_bytes: bytes) -> None:
    parsed = parse_ban_response(response_bytes)
    by_id: dict[str, dict[str, str]] = {}
    for entry in parsed:
        key = entry.get("id_icpe", "")
        if key:
            by_id[key] = entry

    for row in rows:
        key = row.get("id_icpe", "")
        ban = by_id.get(key)
        if not ban:
            row["reverse_label"] = None
            row["reverse_citycode"] = None
            continue
        row["reverse_label"] = ban.get("result_label") or None
        row["reverse_citycode"] = ban.get("result_citycode") or None


# --- Signal pass 5: reserves --------------------------------------------

def find_reserve_membership(
    point: tuple[float, float] | None,
    reserves: list[tuple[str, list[list[list[list[float]]]]]],
) -> tuple[str, float | None]:
    """Returns (reserve_name_or_'none', distance_to_nearest_boundary_m).

    Distance is negative if the point is inside any reserve, positive
    if outside (distance to nearest boundary across all reserves)."""
    if point is None:
        return ("none", None)

    inside_name: str | None = None
    inside_distance: float | None = None
    nearest_outside: float = float("inf")

    for name, polys in reserves:
        is_inside = any(point_in_polygon(point, poly) for poly in polys)
        if is_inside:
            # Compute distance to nearest edge (this is how deep we are)
            d = min(distance_to_polygon_boundary_m(point, poly) for poly in polys)
            if inside_distance is None or d > inside_distance:
                inside_name = name
                inside_distance = d
        else:
            d = min(distance_to_polygon_boundary_m(point, poly) for poly in polys)
            if d < nearest_outside:
                nearest_outside = d

    if inside_name is not None:
        # Negative distance signals "inside, this far from the boundary"
        return (inside_name, -(inside_distance or 0))
    if math.isfinite(nearest_outside):
        return ("none", nearest_outside)
    return ("none", None)


def pass_5_reserves(
    rows: list[AuditedRow],
    reserves: list[tuple[str, list[list[list[list[float]]]]]],
) -> None:
    """Compute reserve membership + distance for stored and geocoded points,
    plus the reserve_ambiguous and reserve_boundary_proximity flags."""
    for row in rows:
        stored_lat = row.get("stored_lat")
        stored_lon = row.get("stored_lon")
        forward_lat = row.get("forward_lat")
        forward_lon = row.get("forward_lon")

        stored_point = (stored_lon, stored_lat) if (stored_lat is not None and stored_lon is not None) else None
        forward_point = (forward_lon, forward_lat) if (forward_lat is not None and forward_lon is not None) else None

        s_name, s_dist = find_reserve_membership(stored_point, reserves)
        g_name, g_dist = find_reserve_membership(forward_point, reserves)

        row["stored_in_reserve"] = s_name
        row["geocoded_in_reserve"] = g_name
        row["stored_reserve_distance_m"] = s_dist
        row["geocoded_reserve_distance_m"] = g_dist

        # ambiguous: stored and geocoded disagree about reserve membership
        row["reserve_ambiguous"] = bool(
            (s_name != g_name)
            and (stored_point is not None or forward_point is not None)
        )

        # boundary proximity: either point is within RESERVE_BOUNDARY_PROXIMITY_M
        # of any boundary, flagged so the reviewer can check the in/out call.
        proximity = False
        for d in (s_dist, g_dist):
            if d is not None and abs(d) < RESERVE_BOUNDARY_PROXIMITY_M:
                proximity = True
                break
        row["reserve_boundary_proximity"] = proximity


# --- Classification & grouping ------------------------------------------

def classify(row: AuditedRow, thresholds: Thresholds) -> AuditClass:
    """First-match-wins ladder. None-safe for forward_distance_m."""
    sentinel_flags = row.get("sentinel_flags", "")
    if "null_island" in sentinel_flags:
        return AuditClass.NULL_ISLAND
    if "outside_gironde" in sentinel_flags:
        return AuditClass.OUTSIDE_GIRONDE

    pip = row.get("pip_in_commune")
    reverse_citycode = row.get("reverse_citycode")
    declared_citycode = row.get("codeInsee", "")
    if pip is False or (reverse_citycode and declared_citycode and reverse_citycode != declared_citycode):
        return AuditClass.WRONG_COMMUNE

    score = row.get("forward_score")
    forward_error = row.get("forward_error", "")
    if forward_error or score is None or score < thresholds["score_cutoff"]:
        # Option B: differentiate "stored coords confirmed in correct commune
        # via reverse" (low priority for review) from "no signal either way"
        # (higher priority).
        rev_cc = row.get("reverse_citycode")
        if rev_cc and declared_citycode and rev_cc == declared_citycode:
            return AuditClass.ADDRESS_UNRESOLVABLE_COMMUNE_OK
        return AuditClass.ADDRESS_UNRESOLVABLE_ISOLATED

    forward_type = row.get("forward_type") or ""
    if forward_type in ("locality", "municipality"):
        return AuditClass.ADDRESS_IMPRECISE

    distance = row.get("forward_distance_m")
    if distance is None:
        return AuditClass.OK
    if distance >= thresholds["very_severe_m"]:
        return AuditClass.VERY_SEVERE
    if distance >= thresholds["severe_m"]:
        return AuditClass.SEVERE
    if distance >= thresholds["suspicious_m"]:
        return AuditClass.SUSPICIOUS
    if distance >= thresholds["minor_m"]:
        return AuditClass.MINOR
    return AuditClass.OK


def assign_group(row: AuditedRow) -> AuditGroup | None:
    """Group priority: reserves > grand > petit. None-safe."""
    if row.get("reserve_ambiguous"):
        return AuditGroup.RESERVES

    distance = row.get("forward_distance_m")
    if (
        row.get("reserve_boundary_proximity")
        and distance is not None
        and distance > 25
    ):
        return AuditGroup.RESERVES

    cls = row.get("audit_class", "")
    if cls in {
        AuditClass.NULL_ISLAND,
        AuditClass.OUTSIDE_GIRONDE,
        AuditClass.WRONG_COMMUNE,
        AuditClass.ADDRESS_UNRESOLVABLE_ISOLATED,
        AuditClass.ADDRESS_IMPRECISE,
        AuditClass.VERY_SEVERE,
        AuditClass.SEVERE,
    }:
        return AuditGroup.GRAND
    if cls == AuditClass.ADDRESS_UNRESOLVABLE_COMMUNE_OK:
        # Lower priority: stored coords confirmed in correct commune via
        # reverse geocoding. Forward failed but reverse confirms commune.
        # Reviewer can typically pick "garder_stored" quickly.
        return AuditGroup.PETIT
    if cls in {AuditClass.SUSPICIOUS, AuditClass.MINOR}:
        return AuditGroup.PETIT
    return None


# --- Hash determinism ---------------------------------------------------

def build_flagged_hash(items: list[FlaggedItem]) -> str:
    """sha256 of '\\n'.join(sorted id_icpe values). Deterministic across runs."""
    ids = sorted(item["id_icpe"] for item in items)
    return "sha256:" + hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


# --- Metadata self-consistency ------------------------------------------

# All audit-output columns documented in metadonnees_colonnes.csv via merge_metadata.
AUDIT_METADATA_ROWS: list[dict[str, str]] = [
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "audit_class",
     "definition": "Classe d'écart attribuée par l'audit (ladder priorité-ordonnée). Valeurs : null_island, outside_gironde, wrong_commune, address_unresolvable_isolated (BAN ne trouve pas l'adresse ET le reverse ne confirme pas la commune), address_unresolvable_commune_ok (BAN ne trouve pas l'adresse mais le reverse confirme la commune — basse priorité), address_imprecise (BAN matche au niveau commune ou locality), very_severe (≥2km), severe (500-2km), suspicious (100-500m), minor (25-100m), ok."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "audit_group",
     "definition": "Groupe de revue : reserves (cas critiques pour les réserves naturelles), grand (≥500m ou structurel), petit (25-500m). Vide pour les lignes ok non concernées par les réserves."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_distance_m",
     "definition": "Distance haversine en mètres entre les coordonnées enregistrées (Géorisques) et les coordonnées géocodées par BAN à partir de l'adresse postale. Vide si BAN n'a pas pu géocoder."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_score",
     "definition": "Score de confiance BAN (0.0 à 1.0) pour le géocodage forward de l'adresse. Sous 0.4 = adresse non résolue."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_type",
     "definition": "Précision du géocodage BAN forward : housenumber (le plus précis), street, locality (commune), municipality."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_label",
     "definition": "Adresse normalisée renvoyée par BAN forward (ex : '15 Rue de la République 33000 Bordeaux')."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_citycode",
     "definition": "Code INSEE de la commune où BAN forward place l'adresse. Doit correspondre à codeInsee si l'audit est cohérent."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_lat",
     "definition": "Latitude WGS84 du point géocodé par BAN forward."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_lon",
     "definition": "Longitude WGS84 du point géocodé par BAN forward."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "forward_error",
     "definition": "Message d'erreur si BAN forward a échoué pour cette ligne. Vide en cas de succès."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "reverse_citycode",
     "definition": "Code INSEE de la commune renvoyé par BAN reverse pour les coordonnées enregistrées. Différence avec codeInsee = signal wrong_commune."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "reverse_label",
     "definition": "Adresse renvoyée par BAN reverse pour les coordonnées enregistrées. Sert d'indication 'qu'est-ce qu'il y a vraiment là'."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "pip_in_commune",
     "definition": "Tristate true/false/empty : le point enregistré est-il à l'intérieur du polygone de la commune déclarée ? Empty si la commune n'est pas dans gironde-communes.geojson."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "sentinel_flags",
     "definition": "Liste séparée par des virgules des sentinelles offline déclenchées : null_island, outside_gironde, commune_centroid, duplicate_coords."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "stored_in_reserve",
     "definition": "Nom de la réserve naturelle qui contient les coordonnées enregistrées, ou 'none' si à l'extérieur de toute réserve."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "geocoded_in_reserve",
     "definition": "Nom de la réserve naturelle qui contient les coordonnées géocodées par BAN, ou 'none'."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "stored_reserve_distance_m",
     "definition": "Distance en mètres entre le point enregistré et la limite de la réserve la plus proche. Négative si à l'intérieur d'une réserve, positive sinon."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "geocoded_reserve_distance_m",
     "definition": "Distance en mètres entre le point géocodé par BAN et la limite de la réserve la plus proche. Même convention de signe que stored_reserve_distance_m."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "reserve_ambiguous",
     "definition": "TRUE si stored_in_reserve != geocoded_in_reserve. Cas critique : la réponse à 'ce site est-il dans une réserve ?' dépend de quel point on regarde."},
    {"fichier": OWNER_FICHIER, "nom_original": "(calculé)", "alias": "reserve_boundary_proximity",
     "definition": "TRUE si l'un des deux points (enregistré ou géocodé) est à moins de 200m d'une limite de réserve. Cas frontaliers à inspecter même si reserve_ambiguous = FALSE."},
]


def assert_metadata_self_consistent(audit_row_columns: set[str]) -> None:
    """Internal sanity check (DD #12): every CSV column we write must have
    a metadata entry, and vice versa. Catches developer drift."""
    metadata_aliases = {row["alias"] for row in AUDIT_METADATA_ROWS}
    if audit_row_columns != metadata_aliases:
        missing_meta = audit_row_columns - metadata_aliases
        extra_meta = metadata_aliases - audit_row_columns
        raise AssertionError(
            f"Audit metadata drift: missing metadata rows for "
            f"{sorted(missing_meta)!r}, extra metadata rows without CSV "
            f"columns for {sorted(extra_meta)!r}. Update AUDIT_METADATA_ROWS "
            f"to match the columns written by build_audit_csv_row()."
        )


# --- Output writers -----------------------------------------------------

# Columns of the FULL audit CSV in canonical order.
# Original Géorisques columns first, then audit columns in metadata order.
AUDIT_FULL_COLUMNS: list[str] = [
    # Identity (from bulk)
    "codeAiot", "id_icpe", "raisonSociale", "libelle_complet", "numeroSiret",
    # Address (from bulk)
    "adresse1", "adresse2", "adresse3", "codePostal", "codeInsee", "commune",
    "nom_commune", "epci_siren", "epci_nom",
    # Geo (stored)
    "longitude", "latitude",
    # Géorisques metadata
    "regimeVigueur", "statutSeveso", "prioriteNationale", "ied", "url",
    # Audit columns (must match AUDIT_METADATA_ROWS keys)
    "audit_class", "audit_group",
    "forward_distance_m", "forward_score", "forward_type", "forward_label",
    "forward_citycode", "forward_lat", "forward_lon", "forward_error",
    "reverse_citycode", "reverse_label",
    "pip_in_commune", "sentinel_flags",
    "stored_in_reserve", "geocoded_in_reserve",
    "stored_reserve_distance_m", "geocoded_reserve_distance_m",
    "reserve_ambiguous", "reserve_boundary_proximity",
]

# The subset of AUDIT_FULL_COLUMNS that are owned by this script
# (used for metadata self-consistency check).
AUDIT_OWNED_COLUMNS: set[str] = {
    "audit_class", "audit_group",
    "forward_distance_m", "forward_score", "forward_type", "forward_label",
    "forward_citycode", "forward_lat", "forward_lon", "forward_error",
    "reverse_citycode", "reverse_label",
    "pip_in_commune", "sentinel_flags",
    "stored_in_reserve", "geocoded_in_reserve",
    "stored_reserve_distance_m", "geocoded_reserve_distance_m",
    "reserve_ambiguous", "reserve_boundary_proximity",
}


def _format_value(v: str | float | bool | None) -> str:
    """Stringify a row value for CSV output. None → '', bool → TRUE/FALSE."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        # Drop trailing zeros while keeping precision; never use scientific notation
        return f"{v:.6f}".rstrip("0").rstrip(".") if math.isfinite(v) else ""
    return str(v)


def write_full_csv(rows: list[AuditedRow], path: Path) -> None:
    """Write all 2890 audited rows with full audit columns. Atomic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path) as h:
        writer = csv.DictWriter(h, fieldnames=AUDIT_FULL_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _format_value(row.get(col)) for col in AUDIT_FULL_COLUMNS})
    print(f"[audit] écrit {path.relative_to(PROJECT_ROOT)} ({len(rows)} lignes)")


def build_flagged_item(row: AuditedRow) -> FlaggedItem:
    """Convert an audited row into the compact item shape used by the review tool."""
    address_parts = [
        row.get("adresse1", "").strip(),
        row.get("adresse2", "").strip(),
        row.get("adresse3", "").strip(),
    ]
    return {
        "id_icpe": row.get("id_icpe", ""),
        "nom_complet": row.get("libelle_complet") or row.get("raisonSociale", ""),
        "siret": row.get("numeroSiret", ""),
        "adresse": ", ".join(p for p in address_parts if p),
        "code_postal": row.get("codePostal", ""),
        "commune": row.get("nom_commune") or row.get("commune", ""),
        "code_insee": row.get("codeInsee", ""),
        "regime_icpe": row.get("regimeVigueur", ""),
        "categorie_seveso": row.get("statutSeveso", ""),
        "priorite_nationale": row.get("prioriteNationale", "").lower() == "true",
        "directive_ied": row.get("ied", "").lower() == "true",
        "stored_lat": row.get("stored_lat"),
        "stored_lon": row.get("stored_lon"),
        "geocoded_lat": row.get("forward_lat"),
        "geocoded_lon": row.get("forward_lon"),
        "geocoded_label": row.get("forward_label") or "",
        "geocoded_score": row.get("forward_score"),
        "geocoded_type": row.get("forward_type") or "",
        "reverse_label": row.get("reverse_label") or "",
        "forward_distance_m": row.get("forward_distance_m"),
        "audit_class": row.get("audit_class", ""),
        "stored_in_reserve": row.get("stored_in_reserve", "none"),
        "geocoded_in_reserve": row.get("geocoded_in_reserve", "none"),
        "stored_reserve_distance_m": row.get("stored_reserve_distance_m"),
        "geocoded_reserve_distance_m": row.get("geocoded_reserve_distance_m"),
        "reserve_ambiguous": bool(row.get("reserve_ambiguous")),
        "reserve_boundary_proximity": bool(row.get("reserve_boundary_proximity")),
        "url_fiche_georisques": row.get("url", ""),
    }


def write_flagged_json(
    rows: list[AuditedRow],
    bucket_size: int,
    thresholds: Thresholds,
    path: Path,
) -> None:
    """Write flagged.json with 3 groups, sorted items, and a stable hash."""
    by_group: dict[str, list[FlaggedItem]] = {
        AuditGroup.RESERVES.value: [],
        AuditGroup.GRAND.value: [],
        AuditGroup.PETIT.value: [],
    }
    for row in rows:
        group = row.get("audit_group", "")
        if group and group in by_group:
            by_group[group].append(build_flagged_item(row))

    for group_id in by_group:
        by_group[group_id].sort(key=lambda item: item["id_icpe"])

    all_items: list[FlaggedItem] = []
    for group_id in by_group:
        all_items.extend(by_group[group_id])

    # audit_run_id is intentionally derived from the flagged_hash so the
    # JSON is byte-stable across re-runs with the same inputs. The previous
    # `datetime.now(timezone.utc)` made every run produce a dirty git
    # diff even when the underlying data was identical, and made it
    # impossible to compare two runs of the audit pipeline using ===
    # equality on this field. The flagged_hash itself is computed from
    # sorted id_icpe values, so this id changes if and only if the
    # flagged set changes.
    flagged_hash = build_flagged_hash(all_items)
    output = {
        "audit_run_id": "audit-" + flagged_hash[:12],
        "flagged_hash": flagged_hash,
        "bucket_size": bucket_size,
        "total_sites_audited": len(rows),
        "thresholds": dict(thresholds),
        "groups": [
            {
                "id": "reserves",
                "label": "Écarts critiques — réserves naturelles",
                "description": "Sites où l'adresse et les coordonnées divergent sur l'appartenance à une réserve, ou sont proches d'une limite.",
                "count": len(by_group["reserves"]),
                "items": by_group["reserves"],
            },
            {
                "id": "grand",
                "label": "Grands écarts",
                "description": "Distance ≥ 500 m, commune incorrecte, adresse irrésoluble, ou coordonnées structurellement invalides.",
                "count": len(by_group["grand"]),
                "items": by_group["grand"],
            },
            {
                "id": "petit",
                "label": "Petits écarts",
                "description": "Distance entre 25 m et 500 m, même commune.",
                "count": len(by_group["petit"]),
                "items": by_group["petit"],
            },
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path) as h:
        json.dump(output, h, ensure_ascii=False, indent=2)
        h.write("\n")
    print(
        f"[audit] écrit {path.relative_to(PROJECT_ROOT)} "
        f"(reserves={len(by_group['reserves'])} grand={len(by_group['grand'])} "
        f"petit={len(by_group['petit'])})"
    )


def write_summary_md(
    rows: list[AuditedRow],
    thresholds: Thresholds,
    path: Path,
) -> None:
    """Write the human-readable summary markdown."""
    from collections import Counter

    total = len(rows)
    class_counts = Counter(row.get("audit_class", "") for row in rows)
    group_counts = Counter(row.get("audit_group", "") for row in rows)

    # Distance histogram (100m buckets up to 2km, then >2km)
    def bucket_label(d: float | None) -> str:
        if d is None:
            return "no distance"
        if d >= 2000:
            return "≥2 km"
        b = int(d // 100) * 100
        return f"{b}-{b+100} m"

    histogram = Counter(bucket_label(row.get("forward_distance_m")) for row in rows)

    # Top 20 offenders by forward_distance_m
    sorted_by_distance = sorted(
        (r for r in rows if r.get("forward_distance_m") is not None),
        key=lambda r: r.get("forward_distance_m") or 0,
        reverse=True,
    )[:20]

    # Top 10 reserve_ambiguous cases
    reserve_cases = [r for r in rows if r.get("reserve_ambiguous")][:10]

    lines: list[str] = []
    lines.append(f"# Audit des coordonnées ICPE — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"**Total** : {total} installations\n")
    lines.append("## Seuils utilisés")
    lines.append("")
    for k, v in thresholds.items():
        lines.append(f"- `{k}` = {v}")
    lines.append("")

    lines.append("## Compte par classe")
    lines.append("")
    lines.append("| Classe | Effectif |")
    lines.append("|---|---:|")
    for cls in [c.value for c in AuditClass]:
        lines.append(f"| {cls} | {class_counts.get(cls, 0)} |")
    lines.append("")

    lines.append("## Compte par groupe de revue")
    lines.append("")
    lines.append("| Groupe | Effectif |")
    lines.append("|---|---:|")
    for grp in [g.value for g in AuditGroup]:
        lines.append(f"| {grp} | {group_counts.get(grp, 0)} |")
    lines.append(f"| (non flagué) | {group_counts.get('', 0)} |")
    lines.append("")

    lines.append("## Histogramme des distances forward")
    lines.append("")
    lines.append("| Bucket | Effectif |")
    lines.append("|---|---:|")
    sorted_keys = sorted(
        histogram.keys(),
        key=lambda k: (k == "no distance", k == "≥2 km", k),
    )
    for k in sorted_keys:
        lines.append(f"| {k} | {histogram[k]} |")
    lines.append("")

    lines.append("## Top 20 offenders (forward_distance_m)")
    lines.append("")
    lines.append("| id_icpe | nom | distance (m) | regime | url |")
    lines.append("|---|---|---:|---|---|")
    for r in sorted_by_distance:
        d = r.get("forward_distance_m") or 0
        lines.append(
            f"| {r.get('id_icpe', '')} "
            f"| {(r.get('libelle_complet') or r.get('raisonSociale', ''))[:60]} "
            f"| {d:.0f} "
            f"| {r.get('regimeVigueur', '')} "
            f"| [fiche]({r.get('url', '')}) |"
        )
    lines.append("")

    lines.append("## Cas reserve_ambiguous (top 10)")
    lines.append("")
    if not reserve_cases:
        lines.append("_Aucun cas reserve_ambiguous détecté._")
    else:
        lines.append("| id_icpe | nom | stored_in | geocoded_in |")
        lines.append("|---|---|---|---|")
        for r in reserve_cases:
            lines.append(
                f"| {r.get('id_icpe', '')} "
                f"| {(r.get('libelle_complet') or r.get('raisonSociale', ''))[:50]} "
                f"| {r.get('stored_in_reserve', 'none')[:40]} "
                f"| {r.get('geocoded_in_reserve', 'none')[:40]} |"
            )
    lines.append("")

    lines.append("## Méthodologie")
    lines.append("")
    lines.append("Cinq passes de signaux : sentinelles offline, point-in-polygon commune, ")
    lines.append("BAN forward (api-adresse.data.gouv.fr/search/csv/), BAN reverse, ")
    lines.append("appartenance aux réserves naturelles.")

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path) as h:
        h.write("\n".join(lines))
    print(f"[audit] écrit {path.relative_to(PROJECT_ROOT)}")


# --- Main ---------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit des coordonnées ICPE en Gironde.")
    p.add_argument("--minor-m", type=float, default=DEFAULT_THRESHOLDS["minor_m"])
    p.add_argument("--suspicious-m", type=float, default=DEFAULT_THRESHOLDS["suspicious_m"])
    p.add_argument("--severe-m", type=float, default=DEFAULT_THRESHOLDS["severe_m"])
    p.add_argument("--very-severe-m", type=float, default=DEFAULT_THRESHOLDS["very_severe_m"])
    p.add_argument("--score-cutoff", type=float, default=DEFAULT_THRESHOLDS["score_cutoff"])
    p.add_argument("--bucket-size", type=int, default=25)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    thresholds: Thresholds = {
        "minor_m": args.minor_m,
        "suspicious_m": args.suspicious_m,
        "severe_m": args.severe_m,
        "very_severe_m": args.very_severe_m,
        "score_cutoff": args.score_cutoff,
    }

    # Self-consistency check (catches developer drift)
    assert_metadata_self_consistent(AUDIT_OWNED_COLUMNS)

    if not DONNEES_BULK_ENRICHI_CSV.exists():
        print(
            f"[error] {DONNEES_BULK_ENRICHI_CSV.relative_to(PROJECT_ROOT)} introuvable. "
            f"Lance `python3 scripts/enrichir_libelles.py` d'abord.",
            file=sys.stderr,
        )
        return 2

    try:
        rows = load_bulk_enriched()
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    print(f"[audit] {len(rows)} installations chargées depuis le bulk enrichi")

    # Stamp the normalized identifier in place — rows already typed as AuditedRow.
    for row in rows:
        row["id_icpe"] = normalize_aiot(row["codeAiot"])

    # Load spatial data
    contour = load_gironde_contour()
    commune_polys = load_commune_polygons()
    reserves = load_reserve_polygons()
    print(
        f"[audit] géométries : {len(commune_polys)} communes, "
        f"{len(reserves)} réserves, contour Gironde {'ok' if contour else 'absent'}"
    )

    # 7 signal passes (3a BAN cascade + 3b OpenCage + 3c Nominatim
    # last-resort fallback + 4 reverse + 5 reserves)
    pass_1_sentinels(rows, contour, commune_polys)
    pass_2_commune_pip(rows, commune_polys)
    try:
        pass_3_ban_forward(rows)
    except Exception as exc:
        print(f"[error] pass 3 (BAN forward) a échoué : {exc}", file=sys.stderr)
        return 1
    try:
        pass_3b_opencage_fallback(rows)
    except Exception as exc:
        # Don't fail the audit if OpenCage is unreachable.
        print(
            f"[warn] pass 3b (OpenCage fallback) failed: {exc}. "
            f"Continuing with what BAN cascade resolved.",
            file=sys.stderr,
        )

    # Re-mark commune-level OpenCage successes as pending so the
    # Nominatim cascade gets a chance to find something more specific.
    # The stash is threaded as an explicit value (was previously a
    # module-level dict — see py-arch finding).
    opencage_stash = mark_weak_opencage_for_retry(rows)

    try:
        pass_3c_nominatim_fallback(rows)
    except Exception as exc:
        # Don't fail the audit if Nominatim is unreachable.
        print(
            f"[warn] pass 3c (Nominatim fallback) failed: {exc}. "
            f"Continuing with what BAN+OpenCage resolved.",
            file=sys.stderr,
        )

    # Decide per-row: keep Nominatim's improvement OR restore OpenCage.
    restore_opencage_if_nominatim_didnt_improve(rows, opencage_stash)
    try:
        pass_4_ban_reverse(rows)
    except Exception as exc:
        print(f"[error] pass 4 (BAN reverse) a échoué : {exc}", file=sys.stderr)
        return 1
    pass_5_reserves(rows, reserves)

    # Classification + group assignment. StrEnums assigned directly —
    # AuditClass and AuditGroup are str subtypes at runtime, so no .value
    # coercion is needed and using .value would lose the type-checker
    # contract documented on AuditedRow.
    for row in rows:
        row["audit_class"] = classify(row, thresholds)
        group = assign_group(row)
        row["audit_group"] = group if group else ""

    # Outputs
    write_full_csv(rows, FULL_CSV_PATH)
    write_flagged_json(rows, args.bucket_size, thresholds, FLAGGED_JSON_PATH)
    write_summary_md(rows, thresholds, SUMMARY_MD_PATH)

    # Metadata
    merge_metadata(CARTE_METADATA_CSV, OWNER_FICHIER, AUDIT_METADATA_ROWS)
    print(f"[audit] dictionnaire {CARTE_METADATA_CSV.relative_to(PROJECT_ROOT)} mis à jour")

    return 0


if __name__ == "__main__":
    sys.exit(main())
