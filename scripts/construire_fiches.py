#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
#     "jsonschema>=4.0",
# ]
# ///
"""
construire_fiches.py — Construit le pivot fiches.parquet depuis les sidecars.

Lit les fiches structurées du sidecar ``_fiches.jsonl`` (produit par
``extract_rapports_markdown.py`` v0.2.0), parse les champs labélisés de
chaque fiche body (Thème, Type de suites, Référence réglementaire,
Prescription, Constats, Proposition de suites, Déjà contrôlé), joint
les métadonnées depuis ``rapports-inspection.csv`` et
``liste-icpe-gironde_enrichi.csv``, et écrit le pivot unique.

Prerequis : ``extract_rapports_markdown.py`` v0.2.0 a été exécuté et
le manifest est cohérent (toutes les entrées en extraction_version 0.2.0).

Lit (read-only) :
  - rapports-inspection-markdown/_fiches.jsonl
  - rapports-inspection-markdown/_manifest.jsonl
  - carte/data/rapports-inspection.csv       (join via nom_fichier_local)
  - carte/data/liste-icpe-gironde_enrichi.csv (join via id_icpe)

Produit (tous atomiques) :
  - carte/data/fiches.parquet           (pivot, 1 ligne par fiche ou prose row)
  - carte/data/fiches-meta.json         (count + versions + sha, <1 KB)
  - carte/data/fiches-manifest.jsonl    (append-only provenance)

Met à jour :
  - carte/data/metadonnees_colonnes.csv (merge_metadata owner fiches.parquet)

Usage :
  uv run scripts/construire_fiches.py

Dépendances : duckdb (PEP 723), jsonschema (PEP 723).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _metadonnees_util import (  # noqa: E402
    atomic_write,
    merge_metadata,
    normalize_aiot,
    require_columns,
)
from _paths import (  # noqa: E402
    CARTE_ENRICHI_CSV,
    CARTE_FICHES_MANIFEST,
    CARTE_FICHES_META_JSON,
    CARTE_FICHES_PARQUET,
    CARTE_METADATA_CSV,
    CARTE_RAPPORTS_CSV,
    FICHES_SIDECAR_PATH,
    RAPPORTS_MARKDOWN_DIR,
)

# --- Configuration ---------------------------------------------------------

CONSTRUIRE_VERSION = "0.1.0"
CONSTRUIRE_EXPECTED_EXTRACTION_VERSION = "0.2.0"

FICHE_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "fiche.json"
)
MANIFEST_PATH = RAPPORTS_MARKDOWN_DIR / "_manifest.jsonl"

RAPPORTS_CSV_COLUMNS = {
    "id_icpe", "nom_complet", "siret", "date_inspection",
    "identifiant_fichier", "nom_fichier_local",
    "url_source_georisques", "url_pages", "statut_telechargement",
}
ENRICHI_CSV_COLUMNS = {
    "id_icpe", "nom_commune", "code_insee_commune",
    "regime_icpe", "categorie_seveso", "epci_nom", "epci_siren",
}

OWNER_FILENAME = "fiches.parquet"

# Champs labélisés dans le body d'une fiche DREAL, dans l'ordre attendu.
# Le regex capture tout ce qui suit le label jusqu'au prochain label ou fin.
# Le gabarit DREAL produit parfois des espaces doubles ou triples entre
# les mots d'un label (justification PDF). On utilise \s+ entre chaque
# mot pour absorber toutes les variantes.
_FICHE_LABELS = [
    ("reference_reglementaire", r"Référence\s+réglementaire\s*:\s*"),
    ("theme", r"Thème\(s\)\s*:\s*"),
    ("deja_controle", r"Point\s+de\s+contrôle\s+déjà\s+contrôlé\s*:\s*"),
    ("prescription", r"Prescription\s+contrôlée\s*:\s*"),
    ("constats_body", r"Constats\s*:\s*"),
    ("type_suite", r"Type\s+de\s+suites\s+proposées\s*:\s*"),
    ("proposition_suite", r"Proposition\s+de\s+suites\s*:\s*"),
]
_LABEL_PATTERNS = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _FICHE_LABELS
]
_ALL_LABEL_RE = re.compile(
    "|".join(pattern for _, pattern in _FICHE_LABELS),
    re.IGNORECASE,
)
# Lignes résiduelles à retirer en fin de valeur : numéros de page isolés,
# barres de tableaux, retours à la ligne vides.
_TRAILING_NOISE_RE = re.compile(r"(?:\n\s*(?:\d+(?:/\d+)?|\|+)\s*)+$")


# --- TypedDicts ------------------------------------------------------------

class FicheRow(TypedDict, total=False):
    """Shape d'une ligne de fiches.parquet."""

    fiche_id: str
    fiche_num: str | None
    titre: str | None
    theme: str | None
    reference_reglementaire: str | None
    deja_controle: str | None
    prescription: str | None
    constats_body: str | None
    type_suite: str | None
    proposition_suite: str | None
    sub_section: str | None
    body: str
    body_chars: int
    regions: list[dict[str, object]] | None
    source_pdf: str
    source_md: str
    extraction_method: str
    id_icpe: str
    siret: str
    nom_complet: str
    date_inspection: str
    identifiant_fichier: str
    url_source_georisques: str
    url_pages: str
    url_markdown: str | None
    nom_commune: str | None
    code_insee_commune: str | None
    epci_nom: str | None
    epci_siren: str | None
    regime_icpe: str | None
    categorie_seveso: str | None


# --- Pure functions --------------------------------------------------------


def parse_fiche_labeled_fields(body: str) -> dict[str, str]:
    """Parse les champs labélisés d'un body de fiche DREAL.

    Cherche chaque label **séquentiellement** dans l'ordre du gabarit,
    chaque recherche commençant après la fin du match précédent. Cela
    évite qu'un mot-clé apparaissant dans le contenu d'un champ
    antérieur (ex. "Constats :" dans la prescription) ne vole la
    frontière du vrai label plus loin dans le texte.

    Le contenu d'un label va jusqu'au prochain label trouvé ou
    jusqu'à la fin du body. Les champs absents retournent une chaîne
    vide.
    """
    result: dict[str, str] = {}
    # Chercher les labels dans l'ordre du gabarit, chaque recherche
    # démarrant après le match précédent pour respecter l'ordre
    # documentaire et éviter les faux positifs.
    matches: list[tuple[str, int, int]] = []  # (name, match_start, content_start)
    search_start = 0
    for name, pattern in _LABEL_PATTERNS:
        m = pattern.search(body, search_start)
        if m:
            matches.append((name, m.start(), m.end()))
            search_start = m.end()

    for i, (name, _start, content_start) in enumerate(matches):
        if i + 1 < len(matches):
            content_end = matches[i + 1][1]
        else:
            content_end = len(body)
        value = body[content_start:content_end].strip()
        # Retirer les artifacts en fin de valeur (numéros de page,
        # barres de tableaux) qui s'infiltrent quand le regex
        # capture au-delà de la vraie fin du champ.
        value = _TRAILING_NOISE_RE.sub("", value).strip()
        result[name] = value

    # Remplir les champs manquants
    for name, _ in _LABEL_PATTERNS:
        if name not in result:
            result[name] = ""
    return result


def build_fiche_id(source_pdf_stem: str, seq_index: int | None) -> str:
    """Construit un fiche_id unique et déterministe.

    Utilise un index séquentiel (0-padded, 1-based) plutôt que le
    numéro de fiche DREAL parce que le gabarit DREAL réutilise la
    numérotation des fiches par sous-section (ex. deux fiches N° 1
    dans un même rapport, l'une dans 2-3 et l'autre dans 2-4).
    L'index séquentiel suit l'ordre d'apparition dans le PDF et
    est toujours unique au sein d'un rapport.
    """
    if seq_index is not None:
        return f"{source_pdf_stem}_f{seq_index:02d}"
    return f"{source_pdf_stem}_prose"


def strip_front_matter(markdown_text: str) -> str:
    """Retire le bloc front matter YAML d'un markdown.

    Retourne le texte après le second ``---``. Si pas de front matter,
    retourne le texte tel quel.
    """
    lines = markdown_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return markdown_text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[i + 1:]).strip()
    return markdown_text


# --- I/O -------------------------------------------------------------------


def check_prereqs() -> None:
    """Vérifie que les sources existent et que le manifest est cohérent."""
    missing: list[str] = []
    for label, path in [
        ("Sidecar _fiches.jsonl", FICHES_SIDECAR_PATH),
        ("Manifest _manifest.jsonl", MANIFEST_PATH),
        ("Rapports CSV", CARTE_RAPPORTS_CSV),
        ("Enrichi CSV", CARTE_ENRICHI_CSV),
        ("Schema fiche.json", FICHE_SCHEMA_PATH),
    ]:
        if not path.exists():
            missing.append(f"  - {label} : {path}")
    if missing:
        print(
            "[prereq] fichiers requis absents :\n" + "\n".join(missing),
            file=sys.stderr,
        )
        sys.exit(2)

    # Vérifier que toutes les entrées du manifest sont en v0.2.0
    stale: list[str] = []
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        latest: dict[str, str] = {}
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and "source_pdf" in d:
                latest[d["source_pdf"]] = d.get("extraction_version", "")
    for pdf, version in latest.items():
        if version != CONSTRUIRE_EXPECTED_EXTRACTION_VERSION:
            stale.append(pdf)
    if stale:
        print(
            f"[prereq] {len(stale)} entrées du manifest ne sont pas en "
            f"extraction_version {CONSTRUIRE_EXPECTED_EXTRACTION_VERSION}.\n"
            f"Premières : {stale[:5]}\n"
            f"Run extract_rapports_markdown.py pour les remonter.",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"[prereq] manifest OK ({len(latest)} entrées, toutes v{CONSTRUIRE_EXPECTED_EXTRACTION_VERSION})")


def load_fiches_sidecar() -> list[dict[str, object]]:
    """Charge _fiches.jsonl, latest-wins par source_pdf."""
    latest: dict[str, dict[str, object]] = {}
    with FICHES_SIDECAR_PATH.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and "source_pdf" in d:
                latest[d["source_pdf"]] = d
    print(f"[load] {len(latest)} sidecar entries chargées")
    return list(latest.values())


def load_rapports_csv() -> dict[str, dict[str, str]]:
    """Index nom_fichier_local → row depuis rapports-inspection.csv."""
    index: dict[str, dict[str, str]] = {}
    with CARTE_RAPPORTS_CSV.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require_columns(reader.fieldnames, RAPPORTS_CSV_COLUMNS, CARTE_RAPPORTS_CSV)
        for row in reader:
            filename = row["nom_fichier_local"].strip()
            if filename:
                index[filename] = row
    print(f"[load] {len(index)} lignes indexées depuis rapports-inspection.csv")
    return index


def load_enrichi_csv() -> dict[str, dict[str, str]]:
    """Index id_icpe → row depuis liste-icpe-gironde_enrichi.csv."""
    index: dict[str, dict[str, str]] = {}
    with CARTE_ENRICHI_CSV.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require_columns(reader.fieldnames, ENRICHI_CSV_COLUMNS, CARTE_ENRICHI_CSV)
        for row in reader:
            key = normalize_aiot(row.get("id_icpe", ""))
            if key:
                index[key] = row
    print(f"[load] {len(index)} installations indexées depuis enrichi.csv")
    return index


def build_rows(
    sidecar_entries: list[dict[str, object]],
    rapports_index: dict[str, dict[str, str]],
    enrichi_index: dict[str, dict[str, str]],
) -> list[FicheRow]:
    """Construit la liste de rows pour le parquet.

    Pour chaque PDF :
    - Si le sidecar a des fiches : 1 row par fiche (champs labélisés parsés)
    - Si pas de fiches (pymupdf4llm_generic, failed) : 1 prose row avec
      le texte complet du markdown (sans front matter)
    """
    rows: list[FicheRow] = []
    orphan_ids: list[str] = []

    for entry in sorted(sidecar_entries, key=lambda e: str(e.get("source_pdf", ""))):
        source_pdf = str(entry["source_pdf"])
        extraction_method = str(entry["extraction_method"])
        source_pdf_stem = source_pdf.rsplit(".", 1)[0] if "." in source_pdf else source_pdf

        # Join rapports CSV
        rapport_row = rapports_index.get(source_pdf, {})
        id_icpe = rapport_row.get("id_icpe", "")
        if not id_icpe:
            # Try first rapport row that matches this PDF
            for k, v in rapports_index.items():
                if k == source_pdf:
                    id_icpe = v.get("id_icpe", "")
                    rapport_row = v
                    break

        # Join enrichi CSV
        enrichi_row = enrichi_index.get(normalize_aiot(id_icpe), {}) if id_icpe else {}
        if id_icpe and not enrichi_row:
            orphan_ids.append(id_icpe)

        # Common fields
        source_md = source_pdf_stem + ".md"
        common = {
            "source_pdf": source_pdf,
            "source_md": source_md,
            "extraction_method": extraction_method,
            "id_icpe": rapport_row.get("id_icpe", ""),
            "siret": rapport_row.get("siret", ""),
            "nom_complet": rapport_row.get("nom_complet", ""),
            "date_inspection": rapport_row.get("date_inspection", ""),
            "identifiant_fichier": rapport_row.get("identifiant_fichier", ""),
            "url_source_georisques": rapport_row.get("url_source_georisques", ""),
            "url_pages": rapport_row.get("url_pages", ""),
            "url_markdown": rapport_row.get("url_markdown") or None,
            "nom_commune": enrichi_row.get("nom_commune") or None,
            "code_insee_commune": enrichi_row.get("code_insee_commune") or None,
            "epci_nom": enrichi_row.get("epci_nom") or None,
            "epci_siren": enrichi_row.get("epci_siren") or None,
            "regime_icpe": enrichi_row.get("regime_icpe") or None,
            "categorie_seveso": enrichi_row.get("categorie_seveso") or None,
        }

        fiches = entry.get("fiches", [])
        if fiches and isinstance(fiches, list) and len(fiches) > 0:
            for seq_idx, fiche in enumerate(fiches, start=1):
                body = str(fiche.get("body", ""))
                fields = parse_fiche_labeled_fields(body)
                fiche_num = str(fiche.get("num", ""))
                row: FicheRow = {
                    **common,  # type: ignore[typeddict-item]
                    "fiche_id": build_fiche_id(source_pdf_stem, seq_idx),
                    "fiche_num": fiche_num,
                    "titre": str(fiche.get("titre", "")) or None,
                    "theme": fields["theme"] or None,
                    "reference_reglementaire": fields["reference_reglementaire"] or None,
                    "deja_controle": fields["deja_controle"] or None,
                    "prescription": fields["prescription"] or None,
                    "constats_body": fields["constats_body"] or None,
                    "type_suite": fields["type_suite"] or None,
                    "proposition_suite": fields["proposition_suite"] or None,
                    "sub_section": str(fiche.get("sub_section", "")) or None,
                    "body": body,
                    "body_chars": len(body),
                    "regions": fiche.get("regions") or None,  # type: ignore[typeddict-item]
                }
                rows.append(row)
        else:
            # Prose row — read the full markdown body
            md_path = RAPPORTS_MARKDOWN_DIR / source_md
            if md_path.exists():
                prose_body = strip_front_matter(
                    md_path.read_text(encoding="utf-8")
                )
            else:
                prose_body = ""
            row = {
                **common,  # type: ignore[typeddict-item]
                "fiche_id": build_fiche_id(source_pdf_stem, None),
                "fiche_num": None,
                "titre": None,
                "theme": None,
                "reference_reglementaire": None,
                "deja_controle": None,
                "prescription": None,
                "constats_body": None,
                "type_suite": None,
                "proposition_suite": None,
                "sub_section": None,
                "body": prose_body,
                "body_chars": len(prose_body),
                "regions": None,
            }
            rows.append(row)

    if orphan_ids:
        print(
            f"[warn] {len(orphan_ids)} id_icpe absents de l'enrichi "
            f"(premières: {orphan_ids[:5]})"
        )
    return rows


def validate_rows(rows: list[FicheRow], schema: dict[str, object]) -> None:
    """Valide chaque row contre le JSON Schema. Halt au premier échec."""
    import jsonschema

    for i, row in enumerate(rows):
        try:
            jsonschema.validate(instance=row, schema=schema)
        except jsonschema.ValidationError as exc:
            print(
                f"[SCHEMA] row {i} (fiche_id={row.get('fiche_id', '?')}) "
                f"invalide : {exc.message} (champ : {list(exc.absolute_path)})",
                file=sys.stderr,
            )
            sys.exit(3)


def write_parquet(rows: list[FicheRow], path: Path) -> str:
    """Écrit le parquet via DuckDB et retourne le sha256 du fichier."""
    import duckdb  # lazy import

    # Sérialise les regions en JSON string pour DuckDB
    # (DuckDB peut stocker des structs mais l'écriture via Python dict
    # est plus fiable en passant par du JSON + un CAST)
    rows_for_db = []
    for row in rows:
        r = dict(row)
        r["regions_json"] = json.dumps(r.pop("regions"), ensure_ascii=False) if r.get("regions") is not None else None
        rows_for_db.append(r)

    con = duckdb.connect(":memory:")

    # Colonnes du parquet final = toutes les clés du TypedDict.
    # regions est stockée comme JSON (via une colonne intermédiaire
    # regions_json en VARCHAR, castée à l'export).
    columns = list(FicheRow.__annotations__)
    col_defs = []
    for col in columns:
        if col == "body_chars":
            col_defs.append(f"{col} INTEGER")
        elif col == "regions":
            # Pas de colonne "regions" dans la table intermédiaire :
            # on insère regions_json (VARCHAR) et on cast à l'export.
            continue
        else:
            col_defs.append(f"{col} VARCHAR")
    col_defs.append("regions_json VARCHAR")

    # Créer la table
    con.execute(f"CREATE TABLE fiches ({', '.join(col_defs)})")

    # Insérer les données via executemany (toutes les colonnes SAUF
    # regions, remplacée par regions_json)
    insert_cols = [c for c in columns if c != "regions"] + ["regions_json"]
    placeholders = ", ".join(["?"] * len(insert_cols))
    con.executemany(
        f"INSERT INTO fiches ({', '.join(insert_cols)}) VALUES ({placeholders})",
        [[r.get(c) for c in insert_cols] for r in rows_for_db],
    )

    # Écrire le parquet avec regions_json casté en JSON nommé "regions"
    tmp_path = path.with_suffix(".parquet.tmp")
    select_cols = []
    for col in columns:
        if col == "regions":
            select_cols.append("CAST(regions_json AS JSON) AS regions")
        else:
            select_cols.append(col)

    con.execute(
        f"COPY (SELECT {', '.join(select_cols)} FROM fiches ORDER BY fiche_id) "
        f"TO '{tmp_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    con.close()

    # Atomic replace
    import os
    os.replace(tmp_path, path)

    # Compute sha256
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_meta(
    total_fiches: int,
    total_rapports_parsed: int,
    total_rapports_without_fiches: int,
    total_failed: int,
    parquet_sha256: str,
) -> str:
    """Écrit fiches-meta.json et retourne son sha256."""
    meta = {
        "total_fiches": total_fiches,
        "total_rapports_parsed": total_rapports_parsed,
        "total_rapports_without_fiches": total_rapports_without_fiches,
        "total_failed": total_failed,
        "construire_version": CONSTRUIRE_VERSION,
        "extraction_version": CONSTRUIRE_EXPECTED_EXTRACTION_VERSION,
        "extracted_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "parquet_sha256": parquet_sha256,
    }
    content = json.dumps(meta, ensure_ascii=False, indent=2)
    with atomic_write(CARTE_FICHES_META_JSON) as handle:
        handle.write(content + "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def append_manifest(
    sidecar_sha: str,
    manifest_sha: str,
    rapports_csv_sha: str,
    parquet_sha: str,
    meta_sha: str,
    total_fiches: int,
    total_rapports_parsed: int,
    total_rapports_skipped: int,
) -> None:
    """Ajoute une entrée de provenance à fiches-manifest.jsonl."""
    entry = {
        "step": "construire_fiches",
        "construire_version": CONSTRUIRE_VERSION,
        "source_sidecar_sha256": sidecar_sha,
        "source_manifest_sha256": manifest_sha,
        "source_rapports_csv_sha256": rapports_csv_sha,
        "parquet_sha256": parquet_sha,
        "meta_sha256": meta_sha,
        "total_fiches": total_fiches,
        "total_rapports_parsed": total_rapports_parsed,
        "total_rapports_skipped": total_rapports_skipped,
        "extracted_at": dt.datetime.now().replace(microsecond=0).isoformat(),
    }
    CARTE_FICHES_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with CARTE_FICHES_MANIFEST.open("a", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False)
        handle.write("\n")


def merge_fiches_metadata() -> None:
    """Ajoute les lignes pour fiches.parquet au dictionnaire multi-fichiers."""
    column_metadata: list[dict[str, str]] = []
    for key in FicheRow.__annotations__:
        column_metadata.append({
            "fichier": OWNER_FILENAME,
            "nom_original": "(calculé)",
            "alias": key,
            "definition": _COLUMN_DEFINITIONS.get(key, f"Colonne {key} du pivot fiches.parquet."),
        })
    merge_metadata(CARTE_METADATA_CSV, OWNER_FILENAME, column_metadata)


def compute_file_sha(path: Path) -> str:
    """SHA-256 hex d'un fichier."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# --- Définitions des colonnes pour le dictionnaire -------------------------

_COLUMN_DEFINITIONS: dict[str, str] = {
    "fiche_id": "Identifiant unique de la fiche : {source_pdf_stem}_f{num} ou {source_pdf_stem}_prose.",
    "fiche_num": "Numéro de la fiche (1, 2, ...). Null pour les rapports sans fiches structurées.",
    "titre": "Titre de la fiche de constat. Null pour les prose rows.",
    "theme": "Thème(s) de la fiche (vocabulaire semi-fermé DREAL, séparés par virgule). Null si absent.",
    "reference_reglementaire": "Référence réglementaire citée dans la fiche (arrêté, article de code).",
    "deja_controle": "Point de contrôle déjà contrôlé lors d'une inspection précédente : Oui, Non, Sans Objet.",
    "prescription": "Texte de la prescription contrôlée (souvent multi-ligne, copie de l'article).",
    "constats_body": "Corps des constats de la fiche — observations de l'inspecteur sur le terrain.",
    "type_suite": "Type de suites proposées (vocabulaire semi-fermé : Sans suite, Observation, Mise en demeure, etc.).",
    "proposition_suite": "Texte détaillé de la proposition de suites.",
    "sub_section": "Heading de la sous-section contenant cette fiche (ex. '2-4) Fiches de constats').",
    "body": "Corps complet de la fiche (tous champs labélisés) ou texte complet du markdown pour les prose rows.",
    "body_chars": "Longueur du champ body en caractères.",
    "regions": "Régions visuelles (page 1-based + bbox en points PDF). JSON array, null pour prose rows.",
    "source_pdf": "Nom du fichier PDF source.",
    "source_md": "Nom du fichier markdown correspondant.",
    "extraction_method": "Chemin d'extraction : dreal_parser, pymupdf4llm_generic, ocr_then_*, failed.",
    "id_icpe": "Identifiant ICPE (codeAiot normalisé, sans zéros de tête).",
    "siret": "SIRET de l'exploitant (14 chiffres) ou chaîne vide.",
    "nom_complet": "Libellé désambiguïsé de l'installation.",
    "date_inspection": "Date de l'inspection (YYYY-MM-DD) ou chaîne vide.",
    "identifiant_fichier": "Identifiant opaque du fichier côté Géorisques.",
    "url_source_georisques": "URL canonique du PDF côté Géorisques.",
    "url_pages": "URL GitHub Pages du PDF.",
    "url_markdown": "URL GitHub Pages du markdown. Null si absent.",
    "nom_commune": "Nom de la commune de l'installation (jointure enrichi).",
    "code_insee_commune": "Code INSEE de la commune.",
    "epci_nom": "Nom de l'EPCI.",
    "epci_siren": "SIREN de l'EPCI.",
    "regime_icpe": "Régime ICPE normalisé (AUTORISATION, ENREGISTREMENT, AUTRE, NON_ICPE).",
    "categorie_seveso": "Catégorie Seveso normalisée (NON_SEVESO, SEUIL_BAS, SEUIL_HAUT, ou vide).",
}


# --- Main ------------------------------------------------------------------


def main() -> int:
    check_prereqs()

    # Load data
    sidecar_entries = load_fiches_sidecar()
    rapports_index = load_rapports_csv()
    enrichi_index = load_enrichi_csv()

    # Load schema
    with FICHE_SCHEMA_PATH.open(encoding="utf-8") as f:
        schema = json.load(f)

    # Build rows
    rows = build_rows(sidecar_entries, rapports_index, enrichi_index)
    print(f"[build] {len(rows)} rows construites")

    # Count stats
    fiches_rows = [r for r in rows if r.get("fiche_num") is not None]
    prose_rows = [r for r in rows if r.get("fiche_num") is None]
    failed_rows = [r for r in rows if r.get("extraction_method") == "failed"]
    print(
        f"[build] {len(fiches_rows)} fiches structurées + "
        f"{len(prose_rows)} prose rows "
        f"(dont {len(failed_rows)} failed)"
    )

    # Validate
    print("[validate] validation par ligne contre fiche.json...")
    validate_rows(rows, schema)
    print(f"[validate] {len(rows)} rows valides")

    # Check uniqueness
    ids = [r["fiche_id"] for r in rows]
    if len(ids) != len(set(ids)):
        from collections import Counter
        dupes = [fid for fid, c in Counter(ids).items() if c > 1]
        print(
            f"[ERROR] {len(dupes)} fiche_id en doublon : {dupes[:5]}",
            file=sys.stderr,
        )
        sys.exit(4)
    print(f"[validate] {len(ids)} fiche_id uniques")

    # Write parquet
    parquet_sha = write_parquet(rows, CARTE_FICHES_PARQUET)
    print(f"[write] {CARTE_FICHES_PARQUET.name} ({CARTE_FICHES_PARQUET.stat().st_size:,} bytes)")

    # Write meta
    meta_sha = write_meta(
        total_fiches=len(fiches_rows),
        total_rapports_parsed=len([e for e in sidecar_entries if e.get("fiches")]),
        total_rapports_without_fiches=len([e for e in sidecar_entries if not e.get("fiches")]),
        total_failed=len(failed_rows),
        parquet_sha256=parquet_sha,
    )
    print(f"[write] {CARTE_FICHES_META_JSON.name}")

    # Append manifest
    sidecar_sha = compute_file_sha(FICHES_SIDECAR_PATH)
    manifest_sha = compute_file_sha(MANIFEST_PATH)
    rapports_csv_sha = compute_file_sha(CARTE_RAPPORTS_CSV)
    append_manifest(
        sidecar_sha=sidecar_sha,
        manifest_sha=manifest_sha,
        rapports_csv_sha=rapports_csv_sha,
        parquet_sha=parquet_sha,
        meta_sha=meta_sha,
        total_fiches=len(fiches_rows),
        total_rapports_parsed=len([e for e in sidecar_entries if e.get("fiches")]),
        total_rapports_skipped=len([e for e in sidecar_entries if not e.get("fiches")]),
    )
    print(f"[write] {CARTE_FICHES_MANIFEST.name} (append)")

    # Merge metadata dictionary
    merge_fiches_metadata()

    # Summary
    print()
    print("=== résumé ===")
    print(f"fiches structurées : {len(fiches_rows)}")
    print(f"prose rows         : {len(prose_rows)}")
    print(f"total rows         : {len(rows)}")
    print(f"parquet sha256     : {parquet_sha[:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
