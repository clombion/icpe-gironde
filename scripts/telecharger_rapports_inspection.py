#!/usr/bin/env python3
"""
telecharger_rapports_inspection.py — Téléchargement des rapports d'inspection ICPE.

Télécharge les rapports d'inspection publiables de Géorisques pour les
installations classées de Gironde, les renomme de façon déterministe à
partir du libellé désambiguïsé, et produit un CSV indexant chaque rapport
avec son URL GitHub Pages post-push.

Sources (read-only) :
  - données-georisques/metadataFichierInspection.csv
    Liste des fichiers d'inspection publiables (identifiant, nom, type,
    codeAiot). 1 ligne par rapport.
  - données-georisques/inspection.csv
    Historique des inspections, joignable via identifiantFichier pour
    récupérer la dateInspection (absente de metadataFichierInspection).
  - carte/data/liste-icpe-gironde_enrichi.csv
    Fournit nom_complet (libellé désambiguïsé) et siret pour chaque
    installation, via id_icpe ↔ codeAiot.

Produits (écrits) :
  - rapports-inspection/*.pdf
    Les PDFs eux-mêmes, nommés
    {slug}_{id_icpe}_{date}_{siret}.pdf
    avec fallbacks nosiret / nodate et slug ASCII-safe.
  - rapports-inspection/_404.txt
    Mémoire persistante des identifiants définitivement introuvables
    (HTTP 404). Au prochain run, ces identifiants sont skippés pour
    éviter de retenter inutilement.
  - rapports-inspection/_erreurs.log
    Rapport lisible du dernier run : transitoires + durables, avec
    raison et identifiants. Écrasé à chaque exécution.
  - carte/data/rapports-inspection.csv
    1 ligne par rapport source (incl. les doublons d'identifiant qui
    partagent le même fichier PDF local). Colonnes aliasées lisibles.
  - carte/data/liste-icpe-gironde_enrichi.csv (modifié)
    Ajoute/remplace la colonne nb_rapports_inspection comptant les
    rapports téléchargés avec succès par installation.
  - carte/data/metadonnees_colonnes.csv (mis à jour)
    Ajoute/remplace les lignes décrivant les colonnes de
    rapports-inspection.csv et nb_rapports_inspection dans l'enrichi,
    via le helper _metadonnees_util partagé avec enrichir_libelles.py.

Téléchargement : 3 workers concurrents, 0.5s de pause entre batches,
timeout 30s par requête, retry exponentiel pour 5xx/timeout/réseau,
backoff long pour 429, skip durable pour 404. Idempotent : un fichier
déjà présent sur disque n'est pas retéléchargé.

Dedup : 1 identifiant = 1 fichier PDF local. Quand un même identifiant
est référencé par plusieurs installations (1 seul cas connu sur 1784),
les lignes du CSV partagent le même nom_fichier_local et la même URL.
Le filename utilise les infos de la 1re installation triée par codeAiot.

Usage :
  python3 scripts/telecharger_rapports_inspection.py             # tout
  python3 scripts/telecharger_rapports_inspection.py --limit 5   # test
  python3 scripts/telecharger_rapports_inspection.py --dry-run   # plan only

Stdlib uniquement.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Le helper _metadonnees_util et le module _paths sont au même niveau que ce script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _metadonnees_util import (  # noqa: E402
    atomic_write,
    merge_metadata,
    normalize_aiot,
    require_columns,
)
from _paths import (  # noqa: E402
    PROJECT_ROOT,
    DONNEES_DIR,
    CARTE_ENRICHI_CSV,
    CARTE_RAPPORTS_CSV,
    CARTE_METADATA_CSV,
    RAPPORTS_INSPECTION_DIR,
)

# --- Configuration ---------------------------------------------------------

# Sources
METADATA_FICHIER_INSPECTION = DONNEES_DIR / "metadataFichierInspection.csv"
INSPECTION_CSV = DONNEES_DIR / "inspection.csv"
MANUAL_ENRICHI = CARTE_ENRICHI_CSV

# Sorties
PDF_DIR = RAPPORTS_INSPECTION_DIR
ERREURS_LOG = PDF_DIR / "_erreurs.log"
LOG_404 = PDF_DIR / "_404.txt"
RAPPORTS_CSV = CARTE_RAPPORTS_CSV
METADATA_CSV = CARTE_METADATA_CSV

# URLs
SOURCE_URL_TEMPLATE = (
    "https://www.georisques.gouv.fr/webappReport/ws/installations/inspection/{id}"
)
PAGES_URL_TEMPLATE = (
    "https://bononlouis-del.github.io/"
    "Les-ICPE-en-r-serve-naturelle-nationale/"
    "rapports-inspection/{filename}"
)

# Téléchargement
USER_AGENT = "projet-icpe-ijba/1.0 (journalism education)"
MAX_WORKERS = 3
BATCH_PAUSE = 0.5  # seconds entre deux batches de MAX_WORKERS
REQUEST_TIMEOUT = 30  # seconds
MIN_PDF_SIZE = 1024  # octets, en dessous on considère que c'est une page d'erreur
RETRY_BACKOFF_5XX = (1, 2, 4)  # secondes
RETRY_BACKOFF_429 = (10, 30, 60)  # secondes, plus conservateur
MAX_RETRIES = len(RETRY_BACKOFF_5XX)  # 3, conservé cohérent avec la longueur des backoff lists

# Suffixe (6 derniers caractères de l'identifiant Géorisques) utilisé
# pour désambiguïser les noms de fichiers lorsque 2 rapports distincts
# pour la même installation le même jour produiraient un filename identique.
DEDUP_HASH_LEN = 6

# Sanitisation des noms de fichiers
SLUG_MAX_LEN = 120
DEDUP_SUFFIX = re.compile(r"\s*\(#(\d+)\)")

# Colonnes minimales attendues dans les CSV sources.
REPORTS_SOURCE_COLUMNS = {"codeAiot", "identifiant", "type", "nom"}
INSPECTION_SOURCE_COLUMNS = {"identifiantFichier", "dateInspection"}
MANUAL_ENRICHI_COLUMNS = {"id_icpe", "nom_complet", "siret"}

# Noms des fichiers cible (pour ownership du dictionnaire multi-fichiers)
MANUAL_OUTPUT_FILENAME = "liste-icpe-gironde_enrichi.csv"
RAPPORTS_OUTPUT_FILENAME = "rapports-inspection.csv"


class DownloadStatus(StrEnum):
    """Statuts de téléchargement. Les 2 membres durables font autorité."""

    OK = "ok"
    SKIP = "skip"
    FAIL_404 = "fail_404"
    FAIL_TINY = "fail_tiny"
    FAIL_5XX = "fail_5xx"
    FAIL_429 = "fail_429"
    FAIL_NET = "fail_net"
    FAIL_TRANSITOIRE = "fail_transitoire"  # statut normalisé pour le CSV public
    NOT_PLANNED = "not_planned"


# Set unique de vérité : quels statuts sont considérés comme durables
# (à mémoriser dans _404.txt, à ne plus retenter, à lister dans la
# section "Échecs durables" du _erreurs.log). Utilisé à 4 endroits du
# code pour éviter les divergences entre sites qui ont historiquement
# traité fail_tiny différemment dans chaque branche.
DURABLE_STATUSES: frozenset[str] = frozenset({
    DownloadStatus.FAIL_404,
    DownloadStatus.FAIL_TINY,
})

# Statuts qui signalent un succès (fichier présent sur disque, cohérent
# côté downstream).
SUCCESS_STATUSES: frozenset[str] = frozenset({
    DownloadStatus.OK,
    DownloadStatus.SKIP,
})

# Spécification des colonnes de rapports-inspection.csv.
# Schéma : (source_key, alias, nom_original_metadata, definition).
# Utilisé pour (a) générer le CSV avec des noms lisibles, (b) alimenter
# le dictionnaire metadonnees_colonnes.csv via merge_metadata.
REPORTS_COLUMN_SPEC: list[tuple[str, str, str, str]] = [
    (
        "id_icpe",
        "id_icpe",
        "(calculé)",
        "Identifiant de l'installation classée (codeAiot sans zéros de "
        "tête). Clé de jointure avec liste-icpe-gironde_enrichi.csv.",
    ),
    (
        "nom_complet",
        "nom_complet",
        "(calculé)",
        "Libellé désambiguïsé de l'installation (copié depuis "
        "liste-icpe-gironde_enrichi.csv au moment du téléchargement).",
    ),
    (
        "siret",
        "siret",
        "(calculé)",
        "SIRET de l'exploitant (copié depuis liste-icpe-gironde_enrichi.csv). "
        "Vide si non renseigné dans la source.",
    ),
    (
        "date_inspection",
        "date_inspection",
        "dateInspection",
        "Date de l'inspection (format YYYY-MM-DD), jointe depuis "
        "inspection.csv via identifiantFichier. Vide si absente dans "
        "la source.",
    ),
    (
        "identifiant_fichier",
        "identifiant_fichier",
        "identifiant",
        "Identifiant opaque du fichier côté Géorisques. Clé d'unicité "
        "du fichier PDF.",
    ),
    (
        "type_fichier",
        "type_fichier",
        "type",
        "Type de document selon Géorisques. Toujours 'Rapport "
        "d'inspection publiable' dans ce CSV.",
    ),
    (
        "nom_fichier_source",
        "nom_fichier_source",
        "nom",
        "Nom du fichier tel que fourni par Géorisques (non utilisé pour "
        "le stockage local).",
    ),
    (
        "nom_fichier_local",
        "nom_fichier_local",
        "(calculé)",
        "Nom de fichier local après sanitisation : "
        "{slug_nom_complet}_{id_icpe}_{date}_{siret}.pdf avec fallbacks "
        "nosiret / nodate. Deux lignes peuvent partager le même nom "
        "quand un identifiant est référencé par plusieurs installations "
        "(dedup par identifiant).",
    ),
    (
        "url_source_georisques",
        "url_source_georisques",
        "(calculé)",
        "URL canonique du PDF côté Géorisques (webappReport). Utilisée "
        "par le script pour le téléchargement.",
    ),
    (
        "url_pages",
        "url_pages",
        "(calculé)",
        "URL GitHub Pages du PDF local post-push. S'ouvre directement "
        "dans le navigateur quand on clique.",
    ),
    (
        "statut_telechargement",
        "statut_telechargement",
        "(calculé)",
        "Statut du dernier téléchargement : 'ok' (téléchargé cette "
        "fois-ci), 'skip' (déjà présent), 'fail_404' (absent côté "
        "source, durable), 'fail_transitoire' (timeout/5xx/réseau, "
        "à retenter).",
    ),
    (
        "taille_octets",
        "taille_octets",
        "(calculé)",
        "Taille du fichier PDF local en octets (stat() après écriture). "
        "Vide si pas téléchargé.",
    ),
]

# Entrée à ajouter au dictionnaire pour la colonne que ce script écrit
# dans liste-icpe-gironde_enrichi.csv.
NB_RAPPORTS_METADATA = {
    "fichier": MANUAL_OUTPUT_FILENAME,
    "nom_original": "(calculé)",
    "alias": "nb_rapports_inspection",
    "definition": (
        "Nombre de rapports d'inspection publiables téléchargés avec succès "
        "pour cette installation (écrit par "
        "telecharger_rapports_inspection.py). Seuls les statuts 'ok' et "
        "'skip' comptent. Valeur 0 si aucun rapport téléchargé ou "
        "installation sans inspection publiable."
    ),
}


# --- Helpers purs ----------------------------------------------------------


def sanitize_slug(nom_complet: str) -> str:
    """Transforme un nom complet en slug ASCII-safe, cap à SLUG_MAX_LEN."""
    # Remplace les suffixes de dédup "(#n)" par "-n"
    slug = DEDUP_SUFFIX.sub(r"-\1", nom_complet)
    # Normalise Unicode et strip les accents
    slug = unicodedata.normalize("NFKD", slug)
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    # Remplace l'em-dash et ses variantes par un simple dash
    slug = slug.replace("—", "-").replace("–", "-")
    # Garde uniquement les caractères sûrs (alphanumérique, dash, underscore)
    slug = re.sub(r"[^A-Za-z0-9\-_]+", "-", slug)
    # Compresse les dashes consécutifs
    slug = re.sub(r"-+", "-", slug)
    # Strip les dashes en début / fin
    slug = slug.strip("-_")
    # Cap longueur et re-strip au cas où le cap coupe au milieu d'un dash
    if len(slug) > SLUG_MAX_LEN:
        slug = slug[:SLUG_MAX_LEN].rstrip("-_")
    return slug or "sans-nom"


def build_filename(slug: str, id_icpe: str, date: str, siret: str) -> str:
    """Construit le nom de fichier local selon le template convenu."""
    return f"{slug}_{id_icpe}_{date or 'nodate'}_{siret or 'nosiret'}.pdf"


# normalize_aiot est importé de _metadonnees_util pour éviter la duplication
# avec enrichir_libelles.py et fetch_georisques.py.


def build_source_url(identifiant: str) -> str:
    """URL Géorisques canonique pour télécharger le PDF."""
    return SOURCE_URL_TEMPLATE.format(id=identifiant)


def build_pages_url(filename: str) -> str:
    """URL GitHub Pages post-push pour le PDF local."""
    return PAGES_URL_TEMPLATE.format(filename=filename)


# --- Chargement et jointure ------------------------------------------------


def load_rapports_metadata() -> list[dict[str, str]]:
    """Charge metadataFichierInspection.csv et déjà normalise id_icpe."""
    rows: list[dict[str, str]] = []
    with METADATA_FICHIER_INSPECTION.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        require_columns(
            reader.fieldnames, REPORTS_SOURCE_COLUMNS, METADATA_FICHIER_INSPECTION
        )
        for row in reader:
            rows.append(
                {
                    "id_icpe": normalize_aiot(row["codeAiot"]),
                    "identifiant_fichier": row["identifiant"],
                    "type_fichier": row["type"],
                    "nom_fichier_source": row["nom"],
                }
            )
    print(f"[load] {len(rows)} rapports chargés depuis {METADATA_FICHIER_INSPECTION.name}")
    return rows


def load_inspection_dates() -> dict[str, str]:
    """Index identifiantFichier → dateInspection avec warning sur duplicats.

    Si le même ``identifiantFichier`` apparaît deux fois avec des dates
    différentes (situation théorique de schema drift côté Géorisques),
    on loggue un avertissement et on garde la première date rencontrée
    (comportement déterministe plutôt que last-write-wins).
    """
    index: dict[str, str] = {}
    with INSPECTION_CSV.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        require_columns(reader.fieldnames, INSPECTION_SOURCE_COLUMNS, INSPECTION_CSV)
        for row in reader:
            ident = row.get("identifiantFichier", "").strip()
            date = row.get("dateInspection", "").strip()
            if not ident or not date:
                continue
            if ident in index and index[ident] != date:
                print(
                    f"[warn] identifiantFichier {ident} a deux dates dans "
                    f"{INSPECTION_CSV.name} : {index[ident]!r} (conservée) "
                    f"vs {date!r} (ignorée)"
                )
                continue
            index.setdefault(ident, date)
    print(f"[load] {len(index)} dates d'inspection indexées depuis {INSPECTION_CSV.name}")
    return index


def load_enrichi_lookup() -> dict[str, dict[str, str]]:
    """Index id_icpe → {nom_complet, siret} depuis l'enrichi manuel."""
    index: dict[str, dict[str, str]] = {}
    with MANUAL_ENRICHI.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require_columns(reader.fieldnames, MANUAL_ENRICHI_COLUMNS, MANUAL_ENRICHI)
        for row in reader:
            key = row.get("id_icpe", "").strip()
            if key:
                index[key] = {
                    "nom_complet": row.get("nom_complet", ""),
                    "siret": row.get("siret", ""),
                }
    print(f"[load] {len(index)} installations indexées depuis {MANUAL_ENRICHI.name}")
    return index


def join_all(
    rapports: list[dict[str, str]],
    dates: dict[str, str],
    enrichi: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """Joint dates et infos installation sur chaque rapport.

    Les rapports dont l'installation est absente de l'enrichi (orphelins)
    sont listés en clair dans la sortie plutôt que juste comptés — 0 cas
    observés en pratique mais utile pour diagnostiquer si ça change.
    """
    orphan_ids: list[str] = []
    for row in rapports:
        row["date_inspection"] = dates.get(row["identifiant_fichier"], "")
        if info := enrichi.get(row["id_icpe"]):
            row["nom_complet"] = info["nom_complet"]
            row["siret"] = info["siret"]
        else:
            orphan_ids.append(row["id_icpe"])
            row["nom_complet"] = f"installation-{row['id_icpe']}"
            row["siret"] = ""
    with_date = sum(1 for r in rapports if r["date_inspection"])
    print(
        f"[join] dates trouvées : {with_date}/{len(rapports)}  |  "
        f"orphelins enrichi : {len(orphan_ids)}"
    )
    if orphan_ids:
        print(f"[join] id_icpe orphelins : {sorted(set(orphan_ids))}")
    return rapports


# --- Dedup et nommage ------------------------------------------------------


def assign_local_filenames(rapports: list[dict[str, str]]) -> None:
    """Calcule nom_fichier_local de façon déterministe et sans collision.

    Deux cas à gérer sans perdre de données :

    1. **Dedup (même identifiant, plusieurs installations)** : 1 seul PDF
       côté Géorisques référencé par N installations. Toutes les N lignes
       du CSV partagent le même nom_fichier_local (1 fichier sur disque,
       N lignes pointant vers lui).

    2. **Collision (identifiants différents, même {id_icpe, date, siret, slug})** :
       2 rapports distincts pour la même installation, même jour (ex.
       "Partie publiable" et "Rapport public" d'une même inspection).
       Sans désambiguïsation, leurs filenames seraient identiques et le
       second écraserait le premier sur disque. On ajoute un suffixe
       déterministe composé des 6 derniers caractères de l'identifiant
       Géorisques aux fichiers impliqués dans une collision.

    Le nom de fichier est calculé depuis la 1re installation par tri
    (id_icpe, identifiant) pour rester stable entre runs.
    """
    # Étape 1 — grouper par identifiant (dedup cas 1)
    by_identifier: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rapports:
        by_identifier[row["identifiant_fichier"]].append(row)

    # Étape 2 — filename naïf par identifiant (primary = plus petit id_icpe).
    # Tri NUMÉRIQUE de id_icpe (pas lexicographique) : pour l'identifiant
    # partagé, "100078337" vs "5200969" — l'ordre lex choisirait
    # "100078337" (car '1' < '5') qui est numériquement plus GRAND. L'ordre
    # numérique choisit 5200969, qui est l'installation au plus petit
    # id_icpe = sémantiquement correct.
    candidate: dict[str, str] = {}  # identifiant → filename naïf
    for identifier, group in by_identifier.items():
        group.sort(key=lambda r: int(r["id_icpe"]))
        primary = group[0]
        slug = sanitize_slug(primary["nom_complet"])
        candidate[identifier] = build_filename(
            slug=slug,
            id_icpe=primary["id_icpe"],
            date=primary["date_inspection"],
            siret=primary["siret"],
        )

    # Étape 3 — détection des collisions (cas 2). Deux identifiants
    # différents ne doivent jamais partager un filename naïf.
    filename_to_identifiers: dict[str, list[str]] = defaultdict(list)
    for identifier, filename in candidate.items():
        filename_to_identifiers[filename].append(identifier)

    collision_groups = [
        (fn, ids)
        for fn, ids in filename_to_identifiers.items()
        if len(ids) > 1
    ]
    desambig_count = 0
    for filename, identifiers in collision_groups:
        desambig_count += len(identifiers)
        for identifier in sorted(identifiers):
            # Suffixe déterministe = DEDUP_HASH_LEN derniers caractères
            # de l'identifiant Géorisques. Les identifiants font 32+ chars
            # alphanumériques, 6 suffisent à garantir l'unicité dans une
            # collision.
            suffix = identifier[-DEDUP_HASH_LEN:]
            base, ext = filename.rsplit(".", 1)
            candidate[identifier] = f"{base}_{suffix}.{ext}"

    # Étape 4 — application sur toutes les lignes (partage du filename
    # entre lignes qui partagent l'identifiant, donc le cas 1 est géré
    # par ce partage, pas par la dédup filename).
    shared_identifier_count = 0
    for identifier, group in by_identifier.items():
        filename = candidate[identifier]
        for row in group:
            row["nom_fichier_local"] = filename
            row["url_source_georisques"] = build_source_url(identifier)
            row["url_pages"] = build_pages_url(filename)
        if len(group) > 1:
            shared_identifier_count += 1

    unique_files = len(set(candidate.values()))
    print(
        f"[rename] {len(rapports)} lignes → {unique_files} fichiers uniques"
    )
    if shared_identifier_count:
        print(
            f"[rename] {shared_identifier_count} identifiants partagés "
            f"entre plusieurs installations (dedup — N lignes, 1 fichier)"
        )
    if desambig_count:
        print(
            f"[rename] {desambig_count} fichiers désambiguïsés par suffixe "
            f"d'identifiant ({len(collision_groups)} collisions résolues)"
        )


# --- Téléchargement --------------------------------------------------------


def load_404_memory() -> set[str]:
    """Charge les identifiants définitivement 404 des runs précédents.

    Supporte les lignes de commentaire indentées grâce au strip préalable :
    on compare ``startswith("#")`` sur la ligne strippée, pas la brute.
    """
    if not LOG_404.exists():
        return set()
    with LOG_404.open(encoding="utf-8") as handle:
        return {s for line in handle if (s := line.strip()) and not s.startswith("#")}


def save_404_memory(identifiers: set[str]) -> None:
    """Persiste la liste des 404 durables, triée pour un diff stable."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Identifiants de rapports d'inspection connus comme durables (404 /",
        "# HTTP 500 'Aucun document trouvé' / corps trop petit). Ces",
        "# identifiants ne seront pas retentés au prochain run, voir",
        "# DURABLE_STATUSES dans telecharger_rapports_inspection.py. Éditable",
        "# à la main si tu veux forcer une nouvelle tentative.",
        "",
    ]
    lines.extend(sorted(identifiers))
    with atomic_write(LOG_404) as handle:
        handle.write("\n".join(lines) + "\n")


def fetch_one(url: str, dest: Path) -> tuple[str, int, str]:
    """Télécharge un fichier. Retourne (statut, taille_octets, raison).

    Statuts possibles : voir ``DownloadStatus``. Les membres de
    ``DURABLE_STATUSES`` sont renvoyés tels quels (pas de retry) ; les
    autres échecs sont considérés comme transitoires par la boucle
    ``fetch_with_retry`` englobante.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read()
        if len(data) < MIN_PDF_SIZE:
            return (DownloadStatus.FAIL_TINY, len(data), f"corps < {MIN_PDF_SIZE} octets")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return (DownloadStatus.OK, len(data), "")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return (DownloadStatus.FAIL_404, 0, "HTTP 404")
        if exc.code == 429:
            return (DownloadStatus.FAIL_429, 0, "HTTP 429")
        if 500 <= exc.code < 600:
            # Géorisques renvoie parfois HTTP 500 avec un body JSON
            # {"error":"Internal Server Error","message":"Aucun document trouvé."}
            # C'est sémantiquement un 404 durable (le fichier n'existe plus
            # dans leur backend) mal typé côté serveur. On lit le body pour
            # distinguer les "500 avec message 'Aucun document'" (durables)
            # des vrais 5xx transitoires. except OSError plutôt que bare
            # Exception : on ne veut pas masquer une KeyboardInterrupt ni
            # un bug de programmation, seulement les erreurs de lecture
            # réseau sur le body.
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except OSError:
                body = ""
            lowered = body.lower()
            if "aucun document" in lowered or "document not found" in lowered:
                return (DownloadStatus.FAIL_404, 0, "HTTP 500 (aucun document trouvé côté source)")
            return (DownloadStatus.FAIL_5XX, 0, f"HTTP {exc.code}")
        return (f"fail_{exc.code}", 0, f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return (DownloadStatus.FAIL_NET, 0, f"réseau: {exc.reason}")
    except TimeoutError as exc:
        return (DownloadStatus.FAIL_NET, 0, f"timeout: {exc}")
    except OSError as exc:
        # Catche ConnectionResetError, BrokenPipeError, et autres erreurs
        # socket de bas niveau qui échappent à URLError parce qu'elles
        # surviennent pendant resp.read() (après le début du transfert).
        return (DownloadStatus.FAIL_NET, 0, f"OS: {type(exc).__name__}: {exc}")


def fetch_with_retry(url: str, dest: Path) -> tuple[str, int, str]:
    """Wrapper avec retry exponentiel pour les échecs transitoires.

    MAX_RETRIES tient automatiquement à ``len(RETRY_BACKOFF_5XX)`` pour
    qu'on ne puisse pas diverger si on édite l'un sans l'autre.
    """
    last = (DownloadStatus.FAIL_NET, 0, "aucune tentative")
    for attempt in range(MAX_RETRIES):
        statut, taille, raison = fetch_one(url, dest)
        if statut == DownloadStatus.OK:
            return statut, taille, raison
        if statut in DURABLE_STATUSES:
            return statut, taille, raison
        last = (statut, taille, raison)
        if attempt < MAX_RETRIES - 1:
            if statut == DownloadStatus.FAIL_429:
                time.sleep(RETRY_BACKOFF_429[attempt])
            else:
                time.sleep(RETRY_BACKOFF_5XX[attempt])
    return last


# --- Planification et exécution --------------------------------------------


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Résultat d'une tentative de téléchargement (immuable)."""

    statut: str
    taille: int
    raison: str


def plan_downloads(
    rapports: list[dict[str, str]],
    known_404: set[str],
    limit: int | None,
) -> tuple[list[tuple[str, str, Path]], dict[str, DownloadResult]]:
    """Calcule le plan de téléchargement dedupliqué par identifiant.

    Retourne :
      - plan : liste de (identifiant, url, chemin_destination) à télécharger
      - results : dict identifiant → DownloadResult déjà rempli pour
        les statuts connus avant exécution (skip si existant, fail_404
        si mémoire, not_planned si au-delà de limit).
    """
    # Pré-index des lignes par identifiant pour éviter un scan O(n) par
    # identifiant unique dans la boucle qui suit.
    row_by_identifier: dict[str, dict[str, str]] = {}
    for row in sorted(rapports, key=lambda r: (int(r["id_icpe"]), r["identifiant_fichier"])):
        row_by_identifier.setdefault(row["identifiant_fichier"], row)

    plan: list[tuple[str, str, Path]] = []
    results: dict[str, DownloadResult] = {}
    for identifier, row in row_by_identifier.items():
        dest = PDF_DIR / row["nom_fichier_local"]
        url = row["url_source_georisques"]

        if identifier in known_404:
            results[identifier] = DownloadResult(
                DownloadStatus.FAIL_404, 0, "connu dans _404.txt"
            )
            continue
        if dest.exists():
            results[identifier] = DownloadResult(
                DownloadStatus.SKIP, dest.stat().st_size, "déjà présent"
            )
            continue
        plan.append((identifier, url, dest))

    # Limit : on ne télécharge que les N premiers du plan (déterministe
    # grâce au tri stable plus haut).
    not_planned: list[tuple[str, str, Path]] = []
    if limit is not None and len(plan) > limit:
        not_planned = plan[limit:]
        plan = plan[:limit]
    for identifier, _url, _dest in not_planned:
        results[identifier] = DownloadResult(
            DownloadStatus.NOT_PLANNED, 0, f"au-delà de --limit {limit}"
        )

    return plan, results


def execute_downloads(
    plan: list[tuple[str, str, Path]]
) -> dict[str, DownloadResult]:
    """Télécharge le plan en parallèle avec politesse entre batches."""
    results: dict[str, DownloadResult] = {}
    total = len(plan)
    if total == 0:
        return results

    counter = 0
    for batch_start in range(0, total, MAX_WORKERS):
        batch = plan[batch_start : batch_start + MAX_WORKERS]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:
            future_map = {
                executor.submit(fetch_with_retry, url, dest): (identifier, dest)
                for identifier, url, dest in batch
            }
            for future in concurrent.futures.as_completed(future_map):
                identifier, dest = future_map[future]
                statut, taille, raison = future.result()
                counter += 1
                results[identifier] = DownloadResult(statut, taille, raison)
                label = "ok   " if statut == DownloadStatus.OK else statut.ljust(5)
                print(
                    f"[download] {counter:>4}/{total:<4}  {label}  "
                    f"{dest.name}  ({taille} octets)"
                )
                if raison and statut != DownloadStatus.OK:
                    print(f"           └─ {raison}")

        if batch_start + MAX_WORKERS < total:
            time.sleep(BATCH_PAUSE)

    return results


# --- Écritures -------------------------------------------------------------


def write_rapports_csv(rapports: list[dict[str, str]]) -> None:
    """Écrit carte/data/rapports-inspection.csv atomiquement."""
    alias_fields = [alias for _src, alias, _orig, _def in REPORTS_COLUMN_SPEC]
    with atomic_write(RAPPORTS_CSV) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=alias_fields, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        for row in sorted(
            rapports, key=lambda r: (int(r["id_icpe"]), r["identifiant_fichier"])
        ):
            writer.writerow(
                {alias: row.get(src, "") for src, alias, _, _ in REPORTS_COLUMN_SPEC}
            )
    print(
        f"[write] {RAPPORTS_CSV.relative_to(PROJECT_ROOT)} "
        f"({len(rapports)} lignes, {len(alias_fields)} colonnes)"
    )


def update_manual_enrichi_counts(counts: dict[str, int]) -> None:
    """Ajoute/remplace nb_rapports_inspection dans liste-icpe-gironde_enrichi.csv.

    Écriture atomique : le fichier existant reste intact tant que le
    nouveau n'est pas entièrement écrit. Un crash au milieu ne laisse
    pas une version tronquée qui serait ensuite relue par
    ``enrichir_libelles.py`` et contribuerait des valeurs vides.
    """
    with MANUAL_ENRICHI.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    if "nb_rapports_inspection" not in fields:
        fields.append("nb_rapports_inspection")

    for row in rows:
        key = row.get("id_icpe", "").strip()
        row["nb_rapports_inspection"] = str(counts.get(key, 0))

    with atomic_write(MANUAL_ENRICHI) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"[write] {MANUAL_ENRICHI.relative_to(PROJECT_ROOT)} "
        f"(+1 colonne nb_rapports_inspection, {len(rows)} lignes)"
    )


def write_metadata_rapports() -> None:
    """Merge les entrées de ce script dans le dictionnaire partagé."""
    own_rows = [
        {
            "fichier": RAPPORTS_OUTPUT_FILENAME,
            "nom_original": nom_orig,
            "alias": alias,
            "definition": definition,
        }
        for _src, alias, nom_orig, definition in REPORTS_COLUMN_SPEC
    ]
    merge_metadata(METADATA_CSV, RAPPORTS_OUTPUT_FILENAME, own_rows)


def write_metadata_nb_rapports() -> None:
    """Merge l'entrée nb_rapports_inspection dans le dictionnaire partagé."""
    merge_metadata(METADATA_CSV, MANUAL_OUTPUT_FILENAME, [NB_RAPPORTS_METADATA])


def write_erreurs_log(
    rapports: list[dict[str, str]],
    results: dict[str, DownloadResult],
    started_at: dt.datetime,
) -> tuple[int, int]:
    """Écrit le log humain des erreurs. Retourne (durables, transitoires)."""
    durables: list[tuple[str, dict[str, str], DownloadResult]] = []
    transitoires: list[tuple[str, dict[str, str], DownloadResult]] = []

    # Dédup par identifiant pour ne pas lister le même PDF plusieurs fois
    seen_ids: set[str] = set()
    for row in sorted(
        rapports, key=lambda r: (int(r["id_icpe"]), r["identifiant_fichier"])
    ):
        identifier = row["identifiant_fichier"]
        if identifier in seen_ids:
            continue
        seen_ids.add(identifier)
        result = results.get(identifier)
        if result is None:
            continue
        if result.statut in DURABLE_STATUSES:
            durables.append((identifier, row, result))
        elif result.statut.startswith("fail_"):
            transitoires.append((identifier, row, result))

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(
        f"# Erreurs de téléchargement — run du {started_at.isoformat(timespec='seconds')}"
    )
    lines.append("")
    lines.append(f"Durables (404 / page d'erreur)      : {len(durables)}")
    lines.append(f"Transitoires (timeout, 5xx, réseau) : {len(transitoires)}")
    lines.append("")
    lines.append("## Échecs durables")
    lines.append("")
    if durables:
        for identifier, row, result in durables:
            lines.append(
                f"- {row['nom_fichier_local']}  ({result.statut} : {result.raison})"
            )
            lines.append(
                f"    id_icpe={row['id_icpe']}  identifiant={identifier}"
            )
            lines.append(f"    url={row['url_source_georisques']}")
    else:
        lines.append("_(aucun)_")
    lines.append("")
    lines.append("## Échecs transitoires (à retenter au prochain run)")
    lines.append("")
    if transitoires:
        for identifier, row, result in transitoires:
            lines.append(
                f"- {row['nom_fichier_local']}  ({result.statut} : {result.raison})"
            )
            lines.append(
                f"    id_icpe={row['id_icpe']}  identifiant={identifier}"
            )
    else:
        lines.append("_(aucun)_")
    lines.append("")

    with atomic_write(ERREURS_LOG) as handle:
        handle.write("\n".join(lines))
    print(
        f"[write] {ERREURS_LOG.relative_to(PROJECT_ROOT)} "
        f"({len(durables)} durables, {len(transitoires)} transitoires)"
    )
    return len(durables), len(transitoires)


# --- Application des statuts sur les rapports ------------------------------


def apply_results_to_rapports(
    rapports: list[dict[str, str]],
    results: dict[str, DownloadResult],
) -> None:
    """Inscrit statut_telechargement et taille_octets sur chaque ligne.

    Normalise les statuts internes vers les 4 valeurs publiques du CSV :
    ``ok``, ``skip``, ``fail_404`` (durable), ``fail_transitoire``
    (retentable). Utilise ``DURABLE_STATUSES`` comme source unique de
    vérité pour la classification durable/transitoire.
    """
    for row in rapports:
        result = results.get(row["identifiant_fichier"])
        if result is None:
            row["statut_telechargement"] = DownloadStatus.NOT_PLANNED
            row["taille_octets"] = ""
            continue
        if result.statut in DURABLE_STATUSES:
            row["statut_telechargement"] = DownloadStatus.FAIL_404
        elif result.statut.startswith("fail_"):
            row["statut_telechargement"] = DownloadStatus.FAIL_TRANSITOIRE
        else:
            row["statut_telechargement"] = result.statut
        row["taille_octets"] = str(result.taille) if result.taille else ""


def count_successes_per_installation(
    rapports: list[dict[str, str]],
) -> dict[str, int]:
    """Compte les rapports téléchargés avec succès par id_icpe."""
    counts: dict[str, int] = defaultdict(int)
    for row in rapports:
        if row["statut_telechargement"] in SUCCESS_STATUSES:
            counts[row["id_icpe"]] += 1
    return dict(counts)


# --- Main ------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Télécharge les rapports d'inspection ICPE depuis Géorisques, "
            "les nomme de façon déterministe, et produit rapports-inspection.csv."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite le nombre de téléchargements (test progressif).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Planifie mais ne télécharge rien. Utile pour valider le plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = dt.datetime.now()

    # Prereq checks — les trois sources doivent exister avant qu'on tente
    # quoi que ce soit. Message explicite pour un utilisateur qui aurait
    # sauté une étape du pipeline.
    for path, hint in (
        (METADATA_FICHIER_INSPECTION, "Lance `python3 scripts/fetch_georisques.py` d'abord."),
        (INSPECTION_CSV, "Lance `python3 scripts/fetch_georisques.py` d'abord."),
        (MANUAL_ENRICHI, "Lance `python3 scripts/enrichir_libelles.py` d'abord."),
    ):
        if not path.exists():
            print(
                f"[error] {path.relative_to(PROJECT_ROOT)} introuvable. {hint}",
                file=sys.stderr,
            )
            return 2

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Chargement et jointures
    try:
        rapports = load_rapports_metadata()
        dates = load_inspection_dates()
        enrichi = load_enrichi_lookup()
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    join_all(rapports, dates, enrichi)

    # 2. Nommage et déduplication par identifiant
    assign_local_filenames(rapports)

    # 3. Planification
    known_404 = load_404_memory()
    if known_404:
        print(f"[plan] {len(known_404)} identifiants dans la mémoire _404.txt")
    plan, precomputed_results = plan_downloads(rapports, known_404, args.limit)
    print(
        f"[plan] {len(plan)} à télécharger, "
        f"{sum(1 for r in precomputed_results.values() if r.statut == DownloadStatus.SKIP)} déjà présents, "
        f"{sum(1 for r in precomputed_results.values() if r.statut == DownloadStatus.FAIL_404)} skip 404 connus, "
        f"{sum(1 for r in precomputed_results.values() if r.statut == DownloadStatus.NOT_PLANNED)} non planifiés"
    )

    if args.dry_run:
        print("[dry-run] plan calculé, aucune écriture, exit.")
        print(f"[dry-run] total dans le plan : {len(plan)}")
        for _identifier, _url, dest in plan[:10]:
            print(f"  DRY  {dest.name}")
        if len(plan) > 10:
            print(f"  … et {len(plan) - 10} autres")
        return 0

    # 4. Exécution des téléchargements
    download_results = execute_downloads(plan)

    # 5. Consolidation des résultats (precomputed + download). Utilise
    # l'opérateur | pour cohérence avec la ligne "all_404 = known_404 | new_404"
    # plus bas (et parce que PEP 584 est plus lisible que {**a, **b}).
    results: dict[str, DownloadResult] = precomputed_results | download_results

    # 6. Mise à jour de la mémoire _404.txt : tous les statuts durables
    # sont mémorisés, pas seulement fail_404 (cohérent avec DURABLE_STATUSES).
    new_durables = {
        identifier
        for identifier, result in download_results.items()
        if result.statut in DURABLE_STATUSES
    }
    all_404 = known_404 | new_durables
    if new_durables:
        print(f"[memory] {len(new_durables)} nouveaux durables à mémoriser")
    save_404_memory(all_404)

    # 7. Application des statuts sur les lignes de rapports
    apply_results_to_rapports(rapports, results)

    # 8. Écriture des sorties
    write_rapports_csv(rapports)
    counts = count_successes_per_installation(rapports)
    update_manual_enrichi_counts(counts)
    write_metadata_rapports()
    write_metadata_nb_rapports()
    durables, transitoires = write_erreurs_log(rapports, results, started_at)

    # 9. Résumé
    elapsed = dt.datetime.now() - started_at
    statut_counts = Counter(r["statut_telechargement"] for r in rapports)
    total_size = sum(
        int(r["taille_octets"]) for r in rapports if r["taille_octets"]
    )
    print()
    print("=" * 60)
    print("Téléchargement rapports d'inspection — terminé")
    print(f"  total traités      : {len(rapports)}")
    for statut, n in statut_counts.most_common():
        print(f"    {statut:<20} : {n}")
    print(f"  durables (404+tiny): {durables}")
    print(f"  transitoires       : {transitoires}")
    print(f"  taille totale DL   : {total_size / 1024 / 1024:.1f} Mo")
    print(f"  temps              : {elapsed}")
    print(f"  installations avec ≥1 rapport ok : {len(counts)}")
    print(f"  log erreurs        : {ERREURS_LOG.relative_to(PROJECT_ROOT)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
