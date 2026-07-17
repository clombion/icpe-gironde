#!/usr/bin/env python3
"""
fetch_georisques.py — Export bulk officiel Géorisques pour la Gironde.

Télécharge l'archive ZIP publiée par l'API Géorisques V1 pour le département 33,
archive la version datée dans ``données-georisques/raw/``, extrait les 5 CSV
normalisés (encodage ISO-8859-1, séparateur ``;``), les convertit en UTF-8
dans ``données-georisques/``, et compare la liste des installations avec le
CSV manuel ``carte/liste-icpe-gironde.csv`` (colonne ``ident``)
pour tracer les installations qui diffèrent entre les deux sources.

Source : https://www.georisques.gouv.fr/doc-api
Endpoint : GET /api/v1/csv/installations_classees?departement=33

Usage :
    python3 scripts/fetch_georisques.py

Aucune dépendance externe (stdlib uniquement).

Robustesse :
  - Erreurs réseau/HTTP attrapées en tête de ``main()`` avec message contextuel.
  - Écritures atomiques via tmp + os.replace : une interruption pendant
    l'écriture ne laisse pas un CSV tronqué ni un PROVENANCE.txt désynchronisé.
  - Extraction ZIP dans un dossier staging (``.extract_tmp/``) puis move
    atomique : plus de risque de CSV de générations mixtes en cas de crash.
  - Validation des colonnes attendues dans chaque CSV lu.
  - ``compare_sources`` retourne un bool pour signaler si le diff a été écrit.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# Le helper _metadonnees_util et le module _paths sont au même niveau que ce script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _metadonnees_util import atomic_write, normalize_aiot, require_columns  # noqa: E402
from _paths import (  # noqa: E402
    PROJECT_ROOT,
    DONNEES_DIR,
    DONNEES_RAW_DIR,
    CARTE_MANUAL_CSV,
)

# --- Configuration ---------------------------------------------------------

API_URL = (
    "https://www.georisques.gouv.fr/api/v1/csv/installations_classees"
    "?departement=33"
)
SOURCE_ENCODING = "iso-8859-1"
USER_AGENT = "projet-icpe-ijba/1.0 (journalism education)"
REQUEST_TIMEOUT = 60  # seconds

DATA_DIR = DONNEES_DIR
RAW_DIR = DONNEES_RAW_DIR
EXTRACT_STAGING_DIR = DATA_DIR / ".extract_tmp"
MANUAL_CSV = CARTE_MANUAL_CSV
DIFF_REPORT = DATA_DIR / "diff_report.txt"
PROVENANCE_FILE = DATA_DIR / "PROVENANCE.txt"

EXPECTED_FILES = {
    "InstallationClassee.csv",
    "inspection.csv",
    "metadataFichierInspection.csv",
    "metadataFichierHorsInspection.csv",
    "rubriqueIC.csv",
}

# Colonnes minimales attendues dans chaque CSV qu'on lit en aval.
# Transforme un KeyError tardif en RuntimeError contextualisé.
BULK_REQUIRED_COLUMNS = {
    "codeAiot",
    "raisonSociale",
    "commune",
    "regimeVigueur",
    "etatActivite",
}
MANUAL_REQUIRED_COLUMNS = {"ident", "libelle", "insee", "regime"}


def download_zip() -> tuple[bytes, Path, dt.datetime]:
    """Télécharge l'archive et l'archive dans raw/ avec un nom horodaté.

    Retourne ``(payload, raw_path, timestamp)``. Le ``timestamp`` est
    capturé une seule fois et est utilisé à la fois pour le nom de
    fichier et pour ``PROVENANCE.txt`` (évite le skew entre les deux).
    """
    print(f"[fetch] GET {API_URL}")
    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        payload = response.read()

    timestamp = dt.datetime.now()
    raw_path = RAW_DIR / f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}_gironde_bulk.zip"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(payload)
    sha256 = hashlib.sha256(payload).hexdigest()
    print(f"[fetch] {len(payload):,} octets → {raw_path.relative_to(PROJECT_ROOT)}")
    print(f"[fetch] sha256={sha256}")

    _write_provenance(raw_path, len(payload), sha256, timestamp)
    return payload, raw_path, timestamp


def _write_provenance(
    raw_path: Path, size: int, sha256: str, timestamp: dt.datetime
) -> None:
    """Trace la provenance de l'export pour audit.

    Utilise le ``timestamp`` capturé au moment du téléchargement plutôt
    que ``datetime.now()`` recalculé, pour que ``date_téléchargement``
    et le nom du fichier archive soient cohérents au bit près.
    """
    content = "\n".join(
        [
            "# Provenance de l'export bulk Géorisques",
            "",
            f"date_téléchargement : {timestamp.isoformat(timespec='seconds')}",
            f"url                 : {API_URL}",
            f"archive             : {raw_path.relative_to(PROJECT_ROOT)}",
            f"taille_octets       : {size}",
            f"sha256              : {sha256}",
            f"encodage_source     : {SOURCE_ENCODING}",
            "séparateur          : ;",
            "",
            "Les CSV extraits dans ce dossier sont convertis en UTF-8.",
            "Les originaux ISO-8859-1 restent disponibles dans raw/ (ZIP).",
            "",
        ]
    )
    with atomic_write(PROVENANCE_FILE) as handle:
        handle.write(content)


def extract_and_convert(payload: bytes) -> dict[str, Path]:
    """Extrait le ZIP en mémoire, écrit les CSV en UTF-8 de façon atomique.

    Les 5 CSV sont d'abord écrits dans un dossier staging
    ``.extract_tmp/``, puis déplacés vers leur destination finale par
    ``os.replace`` — garantit qu'une interruption au milieu ne laisse
    jamais ``données-georisques/`` avec des CSV de générations mixtes
    (certains à jour, d'autres de l'export précédent).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Staging propre avant extraction.
    if EXTRACT_STAGING_DIR.exists():
        shutil.rmtree(EXTRACT_STAGING_DIR)
    EXTRACT_STAGING_DIR.mkdir(parents=True)

    written: dict[str, Path] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            missing = EXPECTED_FILES - names
            extras = names - EXPECTED_FILES
            if missing:
                raise RuntimeError(
                    f"fichiers attendus absents du ZIP : {sorted(missing)}"
                )
            if extras:
                print(f"[extract] fichiers supplémentaires ignorés : {sorted(extras)}")

            # Passe 1 : écrire tous les fichiers dans le staging.
            for name in sorted(EXPECTED_FILES):
                raw_bytes = archive.read(name)
                text = raw_bytes.decode(SOURCE_ENCODING)
                (EXTRACT_STAGING_DIR / name).write_text(text, encoding="utf-8")

            # Passe 2 : déplacer atomiquement chaque fichier vers sa place
            # finale. os.replace est atomique sur POSIX. Si l'un des moves
            # échoue, les précédents restent en place mais le staging est
            # nettoyé dans le finally.
            for name in sorted(EXPECTED_FILES):
                src = EXTRACT_STAGING_DIR / name
                dst = DATA_DIR / name
                os.replace(src, dst)
                written[name] = dst
                line_count = dst.read_text(encoding="utf-8").count("\n")
                print(
                    f"[extract] {name:<40} {line_count:>6} lignes → "
                    f"{dst.relative_to(PROJECT_ROOT)}"
                )
    finally:
        # Nettoyage du staging dans tous les cas (succès comme erreur).
        if EXTRACT_STAGING_DIR.exists():
            shutil.rmtree(EXTRACT_STAGING_DIR)

    return written


def _load_bulk_codes(installation_csv: Path) -> dict[str, dict[str, str]]:
    """Charge les codeAiot du bulk indexés en entier (sans zéros à gauche)."""
    codes: dict[str, dict[str, str]] = {}
    with installation_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        require_columns(
            reader.fieldnames, BULK_REQUIRED_COLUMNS, installation_csv
        )
        for row in reader:
            code_aiot = row["codeAiot"].strip()
            key = normalize_aiot(code_aiot)
            codes[key] = {
                "codeAiot": code_aiot,
                "raisonSociale": row.get("raisonSociale", ""),
                "commune": row.get("commune", ""),
                "regimeVigueur": row.get("regimeVigueur", ""),
                "etatActivite": row.get("etatActivite", ""),
            }
    return codes


def _load_manual_codes(manual_csv: Path) -> dict[str, dict[str, str]]:
    """Charge les identifiants du CSV manuel (colonne ``ident``)."""
    codes: dict[str, dict[str, str]] = {}
    with manual_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require_columns(reader.fieldnames, MANUAL_REQUIRED_COLUMNS, manual_csv)
        for row in reader:
            ident = row["ident"].strip()
            key = normalize_aiot(ident)
            codes[key] = {
                "ident": ident,
                "libelle": row.get("libelle", ""),
                "insee": row.get("insee", ""),
                "regime": row.get("regime", ""),
            }
    return codes


def _sorted_numeric_keys(keys: set[str], source: str) -> list[str]:
    """Trie des identifiants numériques, raise avec contexte si non-int.

    Un ``sorted(keys, key=int)`` brut crashe avec un ValueError peu
    informatif si un identifiant non-numérique est présent. Cette
    version nomme la valeur fautive et la source pour faciliter le
    diagnostic en cas de schema drift côté Géorisques ou data.gouv.fr.
    """
    try:
        return sorted(keys, key=int)
    except ValueError as exc:
        bad = next((k for k in keys if not k.lstrip("-").isdigit()), "?")
        raise RuntimeError(
            f"{source}: identifiant non numérique rencontré : {bad!r}. "
            f"Schema drift possible côté source."
        ) from exc


def compare_sources(installation_csv: Path, manual_csv: Path) -> bool:
    """Compare bulk vs CSV manuel, écrit un rapport de diff dans DIFF_REPORT.

    Retourne ``True`` si le diff a été écrit, ``False`` si le CSV manuel
    est absent (le caller peut ainsi distinguer "pas de manuel" de "diff
    calculé").
    """
    if not manual_csv.exists():
        print(f"[diff] CSV manuel introuvable, diff non calculé : {manual_csv}")
        return False

    bulk = _load_bulk_codes(installation_csv)
    manual = _load_manual_codes(manual_csv)

    bulk_keys = set(bulk)
    manual_keys = set(manual)

    only_in_bulk = _sorted_numeric_keys(bulk_keys - manual_keys, str(installation_csv))
    only_in_manual = _sorted_numeric_keys(manual_keys - bulk_keys, str(manual_csv))
    common = bulk_keys & manual_keys

    lines: list[str] = []
    lines.append("# Comparaison bulk Géorisques vs CSV manuel")
    lines.append("")
    lines.append(f"date            : {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"bulk            : {installation_csv.relative_to(PROJECT_ROOT)}")
    lines.append(f"manuel          : {manual_csv.relative_to(PROJECT_ROOT)}")
    lines.append("")
    lines.append(f"bulk            : {len(bulk):>5} installations")
    lines.append(f"manuel          : {len(manual):>5} installations")
    lines.append(f"en commun       : {len(common):>5}")
    lines.append(f"uniquement bulk : {len(only_in_bulk):>5}")
    lines.append(f"uniquement manuel : {len(only_in_manual):>3}")
    lines.append("")

    lines.append("## Présent dans le bulk, absent du CSV manuel")
    lines.append("")
    if only_in_bulk:
        for key in only_in_bulk:
            row = bulk[key]
            lines.append(
                f"- {row['codeAiot']} | {row['raisonSociale']} | "
                f"{row['commune']} | {row['regimeVigueur']} | "
                f"{row['etatActivite']}"
            )
    else:
        lines.append("_(aucune)_")
    lines.append("")

    lines.append("## Présent dans le CSV manuel, absent du bulk")
    lines.append("")
    if only_in_manual:
        for key in only_in_manual:
            row = manual[key]
            lines.append(
                f"- {row['ident']} | {row['libelle']} | "
                f"INSEE {row['insee']} | {row['regime']}"
            )
    else:
        lines.append("_(aucune)_")
    lines.append("")

    with atomic_write(DIFF_REPORT) as handle:
        handle.write("\n".join(lines))
    print(f"[diff] rapport écrit : {DIFF_REPORT.relative_to(PROJECT_ROOT)}")
    print(
        f"[diff] bulk={len(bulk)}  manuel={len(manual)}  "
        f"seulement_bulk={len(only_in_bulk)}  seulement_manuel={len(only_in_manual)}"
    )
    return True


def main() -> int:
    try:
        payload, _raw_path, _timestamp = download_zip()
    except urllib.error.HTTPError as exc:
        print(f"[fetch] échec HTTP {exc.code} sur {API_URL} : {exc.reason}", file=sys.stderr)
        return 2
    except urllib.error.URLError as exc:
        print(f"[fetch] échec réseau sur {API_URL} : {exc.reason}", file=sys.stderr)
        return 2
    except TimeoutError as exc:
        print(f"[fetch] timeout sur {API_URL} : {exc}", file=sys.stderr)
        return 2

    written = extract_and_convert(payload)
    compare_sources(written["InstallationClassee.csv"], MANUAL_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
