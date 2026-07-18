"""
_tags_util.py — Forme canonique des codes de tags (partagé merge/status).

La taxonomie autorise deux écritures du même code : forme longue
(``R14_NEUTRE_NC``) et forme courte (``R14``). Les agents ont émis les deux
selon les batches. La validation d'appartenance accepte les deux — mais un
``SELECT DISTINCT`` en aval scinde le même code en deux lignes. La forme
canonique lève cette ambiguïté : elle est appliquée à l'écriture du pivot
de tags, pas laissée à charge de chaque lecteur.

Règle : un code axé numériquement (``G1``, ``R14``, ``D01``, ``M08``) se
réduit à son préfixe ``[A-Z]+[0-9]+``. Les modificateurs (``m_DELAI``,
``m_MENACE``) ne matchent pas ce motif et sont conservés entiers.

Pas de dépendance externe : stdlib uniquement.
"""

from __future__ import annotations

import re

_CODE_PREFIX = re.compile(r"[A-Z]+[0-9]+")


def canonical_code(value: str) -> str:
    """Forme canonique d'un code unique. ``G1_OBSERVATION`` → ``G1`` ;
    ``m_DELAI`` inchangé."""
    prefix = str(value).split("_")[0]
    return prefix if _CODE_PREFIX.fullmatch(prefix) else str(value)


def canonical_list(values: list[str] | None) -> list[str]:
    """Forme canonique d'une liste multi-label, en préservant l'ordre et
    en dédupliquant."""
    seen: dict[str, None] = {}
    for v in values or []:
        seen.setdefault(canonical_code(v), None)
    return list(seen)
