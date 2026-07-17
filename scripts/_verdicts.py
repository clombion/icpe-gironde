"""Verdict values shared between the audit review tools.

The canonical source of truth is audit/lib.js:VALID_VERDICTS. This
Python StrEnum mirrors it so that apply_corrections.py, enrichir_libelles.py,
and any future script consuming review files can validate verdicts
without hardcoding strings.
"""
from __future__ import annotations

import enum


class Verdict(enum.StrEnum):
    GARDER_STORED = "garder_stored"
    UTILISER_GEOCODED = "utiliser_geocoded"
    PLACER_MANUELLEMENT = "placer_manuellement"
    TERRAIN = "terrain"
