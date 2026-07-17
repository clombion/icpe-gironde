"""
_paths.py — Chemins canoniques du dépôt projet_icpe.

Source de vérité unique pour les chemins filesystem référencés par les
scripts du pipeline. Renommer un répertoire ou bouger un fichier de
sortie se fait en éditant ce seul module ; les scripts qui importent
les constantes restent intacts.

Convention : tous les chemins sont des ``pathlib.Path`` absolus, ancrés
à ``PROJECT_ROOT`` calculé depuis ``__file__``. Aucun side-effect au
moment de l'import (pas de mkdir, pas d'I/O) — purement déclaratif.

Pas de dépendance externe : stdlib uniquement.
"""

from __future__ import annotations

from pathlib import Path

# scripts/_paths.py → projet_icpe/
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# --- Carte interactive (assets publics + données consommées par la map) -
CARTE_DIR = PROJECT_ROOT / "carte"
CARTE_DATA_DIR = CARTE_DIR / "data"
CARTE_MANUAL_CSV = CARTE_DIR / "liste-icpe-gironde.csv"
CARTE_ENRICHI_CSV = CARTE_DATA_DIR / "liste-icpe-gironde_enrichi.csv"
CARTE_METADATA_CSV = CARTE_DATA_DIR / "metadonnees_colonnes.csv"
CARTE_RAPPORTS_CSV = CARTE_DATA_DIR / "rapports-inspection.csv"
CARTE_COMMUNE_EPCI_CACHE = CARTE_DATA_DIR / "gironde-commune-epci.json"

# --- Source canonique Géorisques (bulk officiel) ------------------------
DONNEES_DIR = PROJECT_ROOT / "données-georisques"
DONNEES_BULK_CSV = DONNEES_DIR / "InstallationClassee.csv"
DONNEES_BULK_ENRICHI_CSV = DONNEES_DIR / "InstallationClassee_enrichi.csv"
DONNEES_RAW_DIR = DONNEES_DIR / "raw"

# --- Pipeline d'audit des coordonnées (ajouté en Phase 3) ---------------
DONNEES_AUDIT_DIR = DONNEES_DIR / "audit"
DONNEES_AUDIT_CACHE_DIR = DONNEES_AUDIT_DIR / ".cache"
DONNEES_AUDIT_REVIEWS_DIR = DONNEES_AUDIT_DIR / "coordonnees-audit-reviews"
FLAGGED_JSON_PATH = DONNEES_AUDIT_DIR / "coordonnees-audit-flagged.json"
CORRECTIONS_CSV = DONNEES_AUDIT_DIR / "coordonnees-corrections.csv"

# --- Autres répertoires de premier niveau -------------------------------
RAPPORTS_INSPECTION_DIR = PROJECT_ROOT / "rapports-inspection"
RAPPORTS_MARKDOWN_DIR = PROJECT_ROOT / "rapports-inspection-markdown"
FICHES_SIDECAR_PATH = RAPPORTS_MARKDOWN_DIR / "_fiches.jsonl"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TESTS_DIR = SCRIPTS_DIR / "tests"

# --- Artefacts construire_fiches (ajoutés en Phase B rapports) ----------
CARTE_FICHES_PARQUET = CARTE_DATA_DIR / "fiches.parquet"
CARTE_FICHES_META_JSON = CARTE_DATA_DIR / "fiches-meta.json"
CARTE_FICHES_MANIFEST = CARTE_DATA_DIR / "fiches-manifest.jsonl"
CARTE_FICHES_SQLITE = CARTE_DATA_DIR / "fiches.sqlite"
