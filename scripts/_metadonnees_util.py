"""
_metadonnees_util.py — Helpers stdlib partagés par les scripts du pipeline.

Fournit deux familles de fonctions réutilisées par les 3 scripts du pipeline
Géorisques (``fetch_georisques.py``, ``enrichir_libelles.py``,
``telecharger_rapports_inspection.py``) :

1. **Dictionnaire multi-fichiers** (``load_metadata``, ``merge_metadata``)
   — protocole d'ownership coopératif sur le fichier
   ``metadonnees_colonnes.csv`` (chemin canonique défini par
   ``_paths.CARTE_METADATA_CSV``). Chaque script possède les entrées
   correspondant à son propre fichier de données, les lignes appartenant
   aux autres scripts sont préservées verbatim lors des réécritures.

2. **Utilities I/O et parsing** (``atomic_write``, ``require_columns``,
   ``normalize_aiot``) — briques de robustesse partagées : écriture
   atomique (tmp + os.replace), validation des colonnes CSV aux
   frontières, normalisation déterministe des identifiants AIOT.

Protocole d'ownership du dictionnaire :

1. La clé d'unicité est la paire ``(fichier, alias)``.
2. Un script ne peut écrire/modifier que les lignes dont le ``fichier``
   correspond à **son** ``owner_fichier``.
3. À l'écriture, un script :
   - charge l'existant,
   - supprime les lignes ``(owner_fichier, alias_in_own_rows)``,
   - ajoute ses lignes propres,
   - réécrit le fichier entier (atomiquement).
4. Les lignes appartenant à d'autres fichiers sont **préservées verbatim**.

Schéma du CSV de métadonnées : ``fichier, nom_original, alias, definition``

Migration depuis l'ancien schéma 3-colonnes : si ``load_metadata`` détecte
un header différent de ``METADATA_SCHEMA``, elle retourne une liste vide
— le prochain appel à ``merge_metadata`` reconstruit le fichier intégralement
au nouveau schéma.

Pas de dépendance externe : stdlib uniquement.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

METADATA_SCHEMA = ["fichier", "nom_original", "alias", "definition"]


# --- Identifier helpers ----------------------------------------------------


def normalize_aiot(code: str) -> str:
    """Normalise un ``codeAiot`` en strippant les zéros de tête.

    Aligne le format ``codeAiot`` du bulk Géorisques (ex. ``"0005206006"``)
    avec le format ``ident`` du CSV manuel data.gouv.fr (ex. ``"5206006"``)
    pour les jointures. Fallback ``"0"`` si l'entrée n'est que des zéros
    ou est vide — le fallback n'apparaît pas dans les données réelles mais
    empêche une chaîne vide de silencieusement matcher plusieurs clés.
    """
    return code.strip().lstrip("0") or "0"


# --- CSV column validation -------------------------------------------------


def require_columns(
    fieldnames: list[str] | None,
    required: set[str],
    source: Path | str,
) -> None:
    """Lève ``RuntimeError`` si une colonne requise manque dans le CSV.

    À appeler après avoir créé un ``csv.DictReader`` pour valider le
    schéma d'un fichier source avant de lire les lignes. Le but est de
    transformer un ``KeyError`` tardif au milieu de l'algorithme en un
    message d'erreur explicite au load, nommant le fichier et la colonne
    absente.
    """
    present = set(fieldnames or [])
    missing = required - present
    if missing:
        raise RuntimeError(
            f"{source}: colonnes attendues absentes du header "
            f"{sorted(missing)!r}. Colonnes présentes : {sorted(present)!r}"
        )


# --- Atomic file write -----------------------------------------------------


@contextmanager
def atomic_write(
    path: Path,
    encoding: str = "utf-8",
    newline: str = "",
) -> Iterator[TextIO]:
    """Écriture atomique via fichier temporaire + ``os.replace``.

    Garantit qu'une interruption (Ctrl-C, SIGKILL, disque plein) pendant
    l'écriture ne laisse pas ``path`` dans un état tronqué : le fichier
    existant reste intact tant que ``os.replace`` n'a pas été appelé, et
    ``os.replace`` est atomique sur POSIX. Sur crash partiel, le .tmp est
    orphelin mais ``path`` est cohérent.

    Usage::

        with atomic_write(my_path) as handle:
            handle.write("...")
            # tout le contenu est écrit dans my_path.tmp
        # ici os.replace(my_path.tmp, my_path) a eu lieu

    Args:
        path: Destination finale. Le fichier temporaire est
            ``path.with_suffix(path.suffix + ".tmp")``.
        encoding: Encodage d'écriture (défaut utf-8).
        newline: Passé à ``open()`` ; ``""`` par défaut pour le CSV sous
            Windows (évite les CRLF doublés).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding=encoding, newline=newline) as handle:
            yield handle
        os.replace(tmp, path)
    except BaseException:
        # Nettoyage du .tmp en cas d'erreur (y compris KeyboardInterrupt).
        # On ne masque pas l'exception.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


# --- Metadata dictionary ---------------------------------------------------


def load_metadata(path: Path) -> list[dict[str, str]]:
    """Lit le dictionnaire existant, ou retourne [] si absent / schéma legacy.

    Si le fichier existe mais n'utilise pas ``METADATA_SCHEMA`` (par exemple
    l'ancien schéma 3-colonnes avant ce helper), on retourne une liste vide
    : le prochain ``merge_metadata`` réécrira complètement au bon schéma.
    C'est la stratégie de migration automatique.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) != METADATA_SCHEMA:
            return []
        return list(reader)


def merge_metadata(
    path: Path,
    owner_fichier: str,
    owner_rows: list[dict[str, str]],
) -> None:
    """Merge les lignes de l'owner dans le dictionnaire partagé.

    Le script appelant possède les lignes dont ``fichier == owner_fichier``
    et dont ``alias`` est dans ``owner_rows``. Ces lignes sont remplacées ;
    toutes les autres (y compris celles appartenant à d'autres fichiers)
    sont préservées.

    Args:
        path: Chemin du fichier de métadonnées (créé si absent).
        owner_fichier: Nom du fichier de données que l'owner gère
            (ex. "liste-icpe-gironde_enrichi.csv"). Doit correspondre à
            la valeur du champ ``fichier`` dans chaque ``owner_rows``.
        owner_rows: Liste de dicts avec les clés de ``METADATA_SCHEMA``.
            L'ordre de cette liste fixe l'ordre des lignes écrites pour
            cet owner.
    """
    # Garde-fou : chaque ligne owner doit être cohérente.
    for row in owner_rows:
        for key in METADATA_SCHEMA:
            if key not in row:
                raise ValueError(
                    f"owner_rows : clé {key!r} manquante dans {row!r}"
                )
        if row["fichier"] != owner_fichier:
            raise ValueError(
                f"owner_rows : fichier={row['fichier']!r} "
                f"ne correspond pas à owner_fichier={owner_fichier!r}"
            )

    existing = load_metadata(path)
    own_aliases = {row["alias"] for row in owner_rows}

    preserved = [
        row
        for row in existing
        if not (row["fichier"] == owner_fichier and row["alias"] in own_aliases)
    ]

    merged = preserved + owner_rows

    with atomic_write(path) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=METADATA_SCHEMA, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(merged)

    print(
        f"[meta] écrit {path.name} "
        f"({len(merged)} lignes total, {len(owner_rows)} pour {owner_fichier})"
    )
