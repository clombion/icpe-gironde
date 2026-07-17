#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pymupdf>=1.24",
#     "pymupdf4llm>=0.0.17",
#     "jsonschema>=4.0",
# ]
# ///
"""
extract_rapports_markdown.py — Extraction markdown des rapports d'inspection ICPE.

Convertit les PDFs de ``rapports-inspection/`` en fichiers markdown
déterministes dans ``rapports-inspection-markdown/``, en préservant la
provenance et la traçabilité via un front matter YAML strict et un
manifeste append-only.

Stratégie d'extraction
----------------------

Chaque PDF est classifié puis routé vers l'un des chemins suivants :

1. ``dreal_parser`` — PDF texte reconnu comme gabarit DREAL
   Nouvelle-Aquitaine (83 % du corpus). Parsé en sections explicites
   (Contexte, Constats, Suites) via les marqueurs du gabarit. Lecture
   via ``pymupdf.get_text()`` qui reconstruit les mots par coordonnées
   et gère correctement les PDFs dont l'encodage des glyphes est
   char-par-char (variante DREAL qui casse ``pdftotext``).

2. ``pymupdf4llm_generic`` — PDF texte au gabarit non reconnu
   (propositions de suites, courriers, etc.). Conversion via
   ``pymupdf4llm.to_markdown()`` qui préserve les titres, listes et
   tableaux.

3. ``ocr_then_dreal_parser`` / ``ocr_then_pymupdf4llm`` — scans sans
   couche texte (~6 % du corpus). Un appel ``ocrmypdf`` avec
   ``--skip-text --language fra`` ajoute une couche texte au PDF, puis
   on réapplique la classification et le chemin correspondant.

4. ``failed`` — cas résiduel (PDF corrompu, format non supporté). Le
   fichier markdown ne contient que le front matter et une note
   d'erreur ; la raison est loggée dans ``_erreurs.log``.

Sources (read-only)
-------------------

  - ``rapports-inspection/*.pdf`` : les PDFs eux-mêmes
  - ``carte/data/rapports-inspection.csv`` : métadonnées
    joignables via ``nom_fichier_local`` (1 ligne par ligne de CSV,
    plusieurs lignes pouvant partager le même PDF dans le cas de
    l'identifiant partagé).

Produits (écrits)
-----------------

  - ``rapports-inspection-markdown/*.md`` : un fichier markdown par
    PDF (même basename, extension changée). Front matter YAML validé
    contre ``scripts/schemas/markdown_frontmatter.json``.
  - ``rapports-inspection-markdown/_manifest.jsonl`` : append-only,
    1 ligne JSON par extraction réussie, avec ``source_sha256``,
    ``markdown_sha256``, ``extraction_method``, timestamp. Permet
    d'auditer la chaîne de provenance et de détecter un PDF modifié.
  - ``rapports-inspection-markdown/_erreurs.log`` : rapport lisible du
    dernier run (PDFs échoués, raisons).
  - ``carte/data/rapports-inspection.csv`` (modifié) :
    ajoute/remplace la colonne ``url_markdown`` pointant vers la
    version markdown GitHub Pages.
  - ``carte/data/metadonnees_colonnes.csv`` (mis à jour) :
    ajoute/remplace la ligne décrivant ``url_markdown``.

Idempotence
-----------

Un markdown dont le manifest atteste déjà l'extraction au
``source_sha256`` courant n'est pas réécrit. ``--force`` force la
réextraction de toutes les cibles demandées.

Usage
-----

  uvx --from . python scripts/extract_rapports_markdown.py            # tout
  uvx --from . python scripts/extract_rapports_markdown.py --limit 10 # test
  uvx --from . python scripts/extract_rapports_markdown.py --dry-run  # plan
  uvx --from . python scripts/extract_rapports_markdown.py --validate # vérif
  uvx --from . python scripts/extract_rapports_markdown.py --force    # tout retaper

Ou directement via ``uv run scripts/extract_rapports_markdown.py`` qui
résout les dépendances du bloc PEP 723.

Dépendances : pymupdf, pymupdf4llm, jsonschema (déclarées inline PEP 723).
``ocrmypdf`` est appelé comme sous-processus via ``uvx --from ocrmypdf``,
pas importé ; voir Phase 3 du pipeline.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

# Les Phases 3-5 ajouteront les imports stdlib supplémentaires
# (argparse, csv, datetime, subprocess) et les imports locaux
# (atomic_write, merge_metadata, require_columns depuis
# _metadonnees_util) au point où ils sont utilisés.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import (  # noqa: E402
    PROJECT_ROOT,
    CARTE_RAPPORTS_CSV,
    CARTE_METADATA_CSV,
    RAPPORTS_INSPECTION_DIR,
    RAPPORTS_MARKDOWN_DIR,
)

# --- Configuration ---------------------------------------------------------

# Sources (read-only)
PDF_DIR = RAPPORTS_INSPECTION_DIR
RAPPORTS_CSV = CARTE_RAPPORTS_CSV
SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "markdown_frontmatter.json"
)

# Sorties
MARKDOWN_DIR = RAPPORTS_MARKDOWN_DIR
MANIFEST_PATH = MARKDOWN_DIR / "_manifest.jsonl"
ERREURS_LOG = MARKDOWN_DIR / "_erreurs.log"
METADATA_CSV = CARTE_METADATA_CSV

# URL GitHub Pages pour la version markdown rendue. Même base path que
# les PDFs ; GitHub Pages sert les .md comme texte brut ou les rend en
# HTML selon la config.
PAGES_URL_TEMPLATE = (
    "https://bononlouis-del.github.io/"
    "Les-ICPE-en-r-serve-naturelle-nationale/"
    "rapports-inspection-markdown/{filename}"
)

# Version sémantique de l'extracteur. À incrémenter dès qu'un changement
# d'algo invaliderait les snapshots existants :
#   - nouvelle règle de classification ou de parsing
#   - nouveau format de front matter
#   - nouveau routage OCR
# Un bump de version invalide toutes les entrées du manifest via
# ``is_up_to_date``, donc le re-run ré-extrait automatiquement sans
# ``--force`` nécessaire.
EXTRACTION_VERSION = "0.2.0"

# Paramètres d'extraction
SCAN_MIN_CHARS = 32  # en dessous de ce seuil, on considère le PDF comme scan
OCR_LANGUAGE = "fra"
OCR_TIMEOUT = 120  # seconds, par PDF scanné

# Colonnes minimales attendues dans le CSV source.
RAPPORTS_CSV_COLUMNS = {
    "id_icpe",
    "nom_complet",
    "siret",
    "date_inspection",
    "identifiant_fichier",
    "nom_fichier_local",
    "url_source_georisques",
    "url_pages",
    "statut_telechargement",
}

# Nom du fichier cible pour ownership du dictionnaire multi-fichiers.
RAPPORTS_OUTPUT_FILENAME = "rapports-inspection.csv"

# Entrée à ajouter au dictionnaire pour la nouvelle colonne url_markdown.
URL_MARKDOWN_METADATA = {
    "fichier": RAPPORTS_OUTPUT_FILENAME,
    "nom_original": "(calculé)",
    "alias": "url_markdown",
    "definition": (
        "URL GitHub Pages de la version markdown du rapport "
        "(rapports-inspection-markdown/{basename}.md), écrite par "
        "extract_rapports_markdown.py. Vide si l'extraction a échoué "
        "(voir _erreurs.log dans rapports-inspection-markdown/)."
    ),
}


class ExtractionMethod(StrEnum):
    """Chemin d'extraction effectivement emprunté pour un PDF donné.

    Les 4 premiers membres représentent des succès et sont les valeurs
    possibles du champ ``extraction_method`` du front matter. ``FAILED``
    est écrit uniquement dans le front matter des PDFs résiduels que
    l'extracteur n'a pas pu convertir (corruption, format inconnu).

    L'ordre ici n'est pas gratuit : il correspond à l'ordre de
    préférence du pipeline. Si un PDF est classifié ``dreal_parser`` on
    ne passe jamais par ``pymupdf4llm_generic``.
    """

    DREAL_PARSER = "dreal_parser"
    PYMUPDF4LLM_GENERIC = "pymupdf4llm_generic"
    OCR_THEN_DREAL_PARSER = "ocr_then_dreal_parser"
    OCR_THEN_PYMUPDF4LLM = "ocr_then_pymupdf4llm"
    FAILED = "failed"


# Sous-ensemble des méthodes considérées comme des succès. Utilisé au
# moment d'écrire le manifeste et la colonne url_markdown du CSV.
SUCCESS_METHODS: frozenset[str] = frozenset({
    ExtractionMethod.DREAL_PARSER,
    ExtractionMethod.PYMUPDF4LLM_GENERIC,
    ExtractionMethod.OCR_THEN_DREAL_PARSER,
    ExtractionMethod.OCR_THEN_PYMUPDF4LLM,
})


class FrontMatter(TypedDict):
    """Shape exacte du bloc YAML en tête de chaque markdown.

    Mirroir 1:1 du JSON Schema ``markdown_frontmatter.json``. Toute
    divergence (ajout, suppression, renommage) doit être faite aux deux
    endroits et couverte par ``test_frontmatter_schema.py`` qui valide
    un ``FrontMatter`` de test contre le schéma.
    """

    source_pdf: str
    source_sha256: str
    source_bytes: int
    id_icpe: str
    nom_complet: str
    siret: str
    date_inspection: str
    identifiant_fichier: str
    url_source_georisques: str
    url_pages: str
    extraction_method: str
    extraction_version: str
    extracted_at: str


class ManifestEntry(TypedDict):
    """Shape d'une ligne du manifeste append-only ``_manifest.jsonl``.

    Chaque extraction réussie y ajoute une ligne. La ligne la plus
    récente pour un ``source_pdf`` donné est celle qui fait foi, les
    anciennes restent visibles en archive et dans l'historique git.
    """

    source_pdf: str
    source_sha256: str
    markdown_file: str
    markdown_sha256: str
    extraction_method: str
    extraction_version: str
    extracted_at: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Résultat d'une extraction, immutable.

    Produit par le pipeline d'extraction (Phase 4) et consommé par
    l'orchestrateur (Phase 5) qui décide quoi écrire sur disque, quoi
    ajouter au manifeste, et comment mettre à jour le CSV.

    Attributs
    ---------
    method
        Chemin emprunté ; ``FAILED`` signale une extraction impossible.
    markdown
        Contenu markdown complet (front matter YAML inclus). Vide si
        ``method == FAILED`` et que l'extracteur n'a même pas pu
        construire un front matter.
    front_matter
        Front matter effectivement utilisé pour rendre le markdown.
        ``None`` uniquement dans le cas extrême où même le front matter
        de secours n'a pas pu être construit (CSV row mutilée). Le
        caller s'en sert pour alimenter le manifeste et la validation
        schema sans avoir à recalculer sha256 ni parser le YAML.
    error
        Message d'erreur humain si ``method == FAILED``, sinon ``None``.
        Loggué tel quel dans ``_erreurs.log``.
    """

    method: ExtractionMethod
    markdown: str
    front_matter: FrontMatter | None = None
    fiches: list[Fiche] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DrealMetadata:
    """Métadonnées extraites du gabarit DREAL Nouvelle-Aquitaine.

    Chaînes vides (pas ``None``) quand un champ est absent, pour coller
    à la convention du reste du pipeline (`''` = non renseigné, tout
    en restant compatible avec la sérialisation YAML stricte).
    """

    references: str
    code_aiot: str
    date_visite: str
    etablissement: str


@dataclass(frozen=True, slots=True)
class FicheRegion:
    """Région visuelle d'une fiche sur une page du PDF.

    Utilisée par le sidecar ``_fiches.jsonl`` pour permettre au client
    web de cropper un snippet du PDF à la bonne zone. Pages en 1-based
    (convention des viewers PDF), bbox en points PDF (72 pts = 1 inch),
    origine haut-gauche.
    """

    page: int
    bbox: list[float]


@dataclass(frozen=True, slots=True)
class Fiche:
    """Fiche de constat individuelle dans la section 2-4.

    Dans le gabarit DREAL, chaque point de contrôle est documenté par
    une fiche numérotée. La fiche commence par ``N° X : Titre`` et
    contient plusieurs champs (référence réglementaire, prescription,
    constats, suites). On les garde en bloc dans ``body`` : la
    granularité d'extraction interne à la fiche n'est pas garantie
    par le gabarit.
    """

    numero: str
    titre: str
    body: str
    sub_section: str = ""


@dataclass(frozen=True, slots=True)
class DrealSections:
    """Résultat complet du parsing d'un PDF au gabarit DREAL.

    Les sous-sections sont listées dans l'ordre où elles apparaissent
    dans le document (2-1, 2-2, 2-3, 2-4), en préservant ordre et
    nommage pour une sortie markdown déterministe.
    """

    metadata: DrealMetadata
    contexte: str
    subsections: list[tuple[str, str]] = field(default_factory=list)
    fiches: list[Fiche] = field(default_factory=list)


# --- Phase 2 : fonctions pures (clean_text, classify, parse, sanitize) ----

# Marqueurs du gabarit DREAL Nouvelle-Aquitaine. Un PDF est considéré
# comme gabarit standard si les 4 marqueurs sont présents dans le texte
# natif. Observé sur 1627/1782 PDFs du corpus (91,3 %, incluant les
# 47 scans récupérés via OCR).
DREAL_MARKERS: tuple[str, ...] = (
    "Rapport de l'Inspection des installations classées",
    "1) Contexte",
    "2) Constats",
    "Code AIOT",
)

# Regex de parsing. Compilés au niveau module pour éviter la
# recompilation à chaque appel et pour les rendre grep-ables en un
# seul endroit.
_RE_REFERENCES = re.compile(r"Références\s*:\s*([^\n]+)")
_RE_CODE_AIOT = re.compile(r"Code AIOT\s*:\s*([0-9]+)")
_RE_DATE_VISITE = re.compile(
    r"Visite d'inspection du\s+([0-9]{2}/[0-9]{2}/[0-9]{4})"
)
# Nom de l'établissement = ce qui se trouve entre "Publié sur" et
# "Références :". La ligne "Publié sur" est suivie d'un saut de ligne,
# puis du nom (parfois multi-lignes), puis d'une ligne vide ou du
# champ Références.
_RE_ETABLISSEMENT = re.compile(
    r"Publié sur\s*\n\s*\n?(.+?)\n\s*(?:\n|Références\s*:)",
    re.DOTALL,
)
# Marqueurs de sections : ancrés en début de ligne (``(?m)^``) pour ne
# pas matcher des occurrences incidentes au milieu d'un paragraphe
# (sommaire, pied de page, citation). Le split est fait par index
# plutôt que par capture groupe, ce qui évite les mauvaises surprises
# de backtracking avec des regex non-greedy en DOTALL.
_RE_MARKER_CONTEXTE = re.compile(r"(?m)^1\)\s*Contexte\b")
_RE_MARKER_CONSTATS = re.compile(r"(?m)^2\)\s*Constats\b")
# Sous-sections 2-N) Titre. Lookahead sur la prochaine sous-section
# pour délimiter le corps.
_RE_SUBSECTION = re.compile(
    r"(2-\d+\))\s*([^\n]+)\n(.+?)(?=\n\s*2-\d+\)|\Z)",
    re.DOTALL,
)
# Fiches de constat N° X : Titre — le corps s'arrête à la fiche
# suivante ou en fin de bloc.
_RE_FICHE = re.compile(
    r"N°\s*(\d+)\s*:\s*([^\n]+)\n(.+?)(?=\nN°\s*\d+\s*:|\Z)",
    re.DOTALL,
)


def clean_text(text: str) -> str:
    """Normalise le texte brut extrait par pymupdf.

    - Remplace les sauts de page (``\\f``) par des sauts de ligne.
    - Normalise les espaces insécables (``\\xa0``) en espaces simples.
    - Retire les soft hyphens (``\\u00ad``) qui apparaissent parfois
      dans les PDFs générés avec des coupures de mots.
    - Collapse toute séquence de 3+ retours à la ligne en exactement
      2 (préserve les paragraphes sans accumuler de blanc inutile).
    - Strip les espaces/tabs en fin de ligne (invisibles mais qui
      polluent les diffs et les snapshots).
    - Strip les espaces/retours en tout début et fin de texte.

    Volontairement conservateur : ne touche pas aux numéros de page
    en ligne (le risque est de couper accidentellement du contenu
    légitime se terminant par un chiffre).
    """
    text = text.replace("\f", "\n")
    text = text.replace("\xa0", " ")
    text = text.replace("\u00ad", "")
    # Trim trailing whitespace per line
    text = re.sub(r"[ \t]+(?=\n)", "", text)
    # Collapse 3+ newlines to exactly 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def needs_ocr(text: str) -> bool:
    """Détecte un PDF scanné à partir du texte extrait par pymupdf.

    Un PDF sans couche texte produit ``""`` ou quelques caractères
    résiduels (en-têtes, glyphes isolés). Le seuil
    ``SCAN_MIN_CHARS`` (32) est intentionnellement bas : un rapport
    DREAL standard fait plusieurs milliers de caractères, et un
    document texte même court dépasse toujours ce seuil.
    """
    return len(text.strip()) < SCAN_MIN_CHARS


def is_dreal_template(text: str) -> bool:
    """Un PDF est au gabarit DREAL si les 4 marqueurs sont présents.

    Tous-ou-rien plutôt que score : un gabarit partiel est trop fragile
    pour que le parser structuré donne un résultat fiable — on le
    route vers ``pymupdf4llm_generic`` qui fait un rendu neutre.

    Les marqueurs "1) Contexte" et "2) Constats" sont testés en début
    de ligne (BOL) comme dans ``_split_contexte_constats``, pour
    éviter de classifier un PDF comme DREAL alors que le parser
    structuré ne trouverait pas ses marqueurs BOL et produirait
    un body vide.
    """
    # Les 2 premiers marqueurs sont testés en substring simple
    # (pas de risque BOL car ils n'ont pas d'ancrage dans le parser).
    if not all(marker in text for marker in DREAL_MARKERS[:1] + DREAL_MARKERS[3:]):
        return False
    # Les marqueurs de section doivent être en début de ligne,
    # exactement comme les regex du parser les cherche.
    return bool(_RE_MARKER_CONTEXTE.search(text) and _RE_MARKER_CONSTATS.search(text))


def classify_text(text: str) -> ExtractionMethod:
    """Classifie un PDF texte (pas scan) en DREAL ou générique.

    Ne retourne **jamais** un membre ``OCR_THEN_*`` ni ``FAILED`` : le
    caller a déjà pré-filtré les scans via ``needs_ocr()`` et les
    échecs sont détectés au niveau du pipeline (Phase 4).
    """
    if is_dreal_template(text):
        return ExtractionMethod.DREAL_PARSER
    return ExtractionMethod.PYMUPDF4LLM_GENERIC


def _extract_first(pattern: re.Pattern[str], text: str) -> str:
    """Helper : retourne le 1er groupe capturé ou chaîne vide."""
    if match := pattern.search(text):
        return match.group(1).strip()
    return ""


def parse_dreal_sections(text: str) -> DrealSections:
    """Parse un PDF au gabarit DREAL en sections structurées.

    Prérequis : ``is_dreal_template(text)`` renvoie ``True``. Si les
    marqueurs ne sont pas là, le parsing ne panique pas mais produit
    un résultat partiellement vide — c'est au caller de vérifier le
    prérequis pour éviter le silencieux.
    """
    metadata = DrealMetadata(
        references=_extract_first(_RE_REFERENCES, text),
        code_aiot=_extract_first(_RE_CODE_AIOT, text),
        date_visite=_extract_first(_RE_DATE_VISITE, text),
        etablissement=_normalize_etablissement(
            _extract_first(_RE_ETABLISSEMENT, text)
        ),
    )

    contexte, constats = _split_contexte_constats(text)
    subsections = _split_subsections(constats)
    fiches = _extract_fiches_from_subsections(subsections)

    return DrealSections(
        metadata=metadata,
        contexte=contexte,
        subsections=subsections,
        fiches=fiches,
    )


def _split_contexte_constats(text: str) -> tuple[str, str]:
    """Découpe le texte sur les premiers marqueurs 1) Contexte / 2) Constats.

    Stratégie par index plutôt que par capture regex :

    1. Trouver le premier marqueur ``^1) Contexte`` en début de ligne.
    2. Trouver le premier marqueur ``^2) Constats`` qui **suit** le
       précédent. Le lookahead au premier match du regex ``_RE_MARKER_CONSTATS``
       ne garantit pas cet ordre dans l'absolu, mais en itérant
       sur les matches on peut sélectionner le premier qui vient
       après Contexte.
    3. Contexte = tranche [fin-marqueur-contexte : début-marqueur-constats]
    4. Constats = tranche [fin-marqueur-constats : fin-du-texte]

    Si un des marqueurs est absent ou si Contexte vient après
    Constats (ce qui serait anormal), on retourne des chaînes vides :
    le caller (``parse_dreal_sections``) préfère produire un résultat
    partiel que lever une exception qui casserait tout le pipeline.
    """
    contexte_match = _RE_MARKER_CONTEXTE.search(text)
    if not contexte_match:
        return "", ""
    # Cherche le 1er marqueur 2) Constats qui commence APRÈS la fin
    # du marqueur Contexte, pour garantir l'ordre documentaire même
    # si le texte brut contient plusieurs occurrences.
    constats_match = _RE_MARKER_CONSTATS.search(text, contexte_match.end())
    if not constats_match:
        return "", ""
    contexte = text[contexte_match.end() : constats_match.start()].strip()
    constats = text[constats_match.end() :].strip()
    return contexte, constats


def _normalize_etablissement(raw: str) -> str:
    """Aplatit les sauts de ligne multiples en espaces simples.

    Le nom d'établissement est souvent multi-ligne dans le PDF
    (adresse postale complète). On normalise en une seule ligne pour
    qu'il tienne comme titre de markdown et dans le front matter.
    """
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw).strip()


def _split_subsections(constats: str) -> list[tuple[str, str]]:
    """Découpe la section 2) Constats en sous-sections 2-N).

    Retourne une liste de ``(heading, body)`` dans l'ordre du document.
    Si aucune sous-section n'est détectée, retourne une unique entrée
    ``[("Constats", constats)]`` — le contenu n'est jamais perdu.
    """
    if not constats:
        return []
    matches = list(_RE_SUBSECTION.finditer(constats))
    if not matches:
        return [("Constats", constats.strip())]
    out: list[tuple[str, str]] = []
    for m in matches:
        num = m.group(1).strip()
        title = m.group(2).strip()
        body = m.group(3).strip()
        out.append((f"{num} {title}", body))
    return out


def _extract_fiches_from_subsections(
    subsections: list[tuple[str, str]],
) -> list[Fiche]:
    """Extrait les fiches numérotées N° X à partir des sous-sections.

    Dans le gabarit DREAL, les fiches vivent typiquement dans la
    sous-section ``2-4) Fiches de constats`` mais on accepte tout
    bloc contenant le pattern ``N° X :`` pour robustesse. Les fiches
    de toutes les sous-sections sont fusionnées dans l'ordre
    rencontré.
    """
    fiches: list[Fiche] = []
    for heading, body in subsections:
        for match in _RE_FICHE.finditer(body):
            fiches.append(
                Fiche(
                    numero=match.group(1).strip(),
                    titre=match.group(2).strip(),
                    body=match.group(3).strip(),
                    sub_section=heading,
                )
            )
    return fiches


def parse_fiches_constats(constats_text: str) -> list[Fiche]:
    """Version autonome de l'extraction des fiches à partir du texte brut.

    Utile quand on veut traiter un bloc ``constats`` sans être passé
    par ``parse_dreal_sections`` — par exemple dans les tests unitaires
    qui construisent du texte à la main.
    """
    return [
        Fiche(
            numero=m.group(1).strip(),
            titre=m.group(2).strip(),
            body=m.group(3).strip(),
        )
        for m in _RE_FICHE.finditer(constats_text)
    ]


def render_front_matter_yaml(fm: FrontMatter) -> str:
    """Sérialise un ``FrontMatter`` en YAML minimaliste et déterministe.

    Stratégie : chaque valeur est encodée via ``json.dumps`` (YAML est
    un sur-ensemble de JSON, donc tout JSON valide est du YAML
    valide). Ça garantit l'échappement correct des guillemets, accents,
    contrôles, sans dépendre d'une lib YAML tierce.

    L'ordre des clés suit celui des propriétés du ``TypedDict``, qui
    lui-même correspond à l'ordre du JSON Schema — permet des
    snapshots stables.
    """
    lines = ["---"]
    # ``FrontMatter`` est un TypedDict, donc un dict à l'exécution ;
    # on itère sur ses clés dans l'ordre de déclaration du TypedDict
    # pour un rendu déterministe.
    for key in FrontMatter.__annotations__:
        value = fm[key]  # type: ignore[literal-required]
        if isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def render_dreal_markdown(
    sections: DrealSections,
    front_matter: FrontMatter,
) -> str:
    """Assemble un markdown structuré à partir d'un parsing DREAL.

    Structure produite :

    ::

        ---
        <front matter YAML>
        ---

        # Rapport d'inspection — <établissement>

        **Références** : ...
        **Code AIOT** : ...
        **Visite d'inspection** : ...

        ## 1) Contexte

        <contexte>

        ## 2) Constats

        ### 2-1) ...
        ### 2-2) ...
        ### 2-3) ...
        ### 2-4) Fiches de constats

        #### Fiche N° 1 — <titre>

        <body>

        #### Fiche N° 2 — <titre>

        <body>

    Les fiches sont extraites de la sous-section 2-4 qui est remplacée
    par des H4 individuels — une fiche = un H4 indexable. Les autres
    sous-sections restent en H3 avec leur corps textuel tel quel.
    """
    lines: list[str] = []
    lines.append(render_front_matter_yaml(front_matter))
    lines.append("")

    title = sections.metadata.etablissement or "(établissement inconnu)"
    lines.append(f"# Rapport d'inspection — {title}")
    lines.append("")

    meta_lines: list[str] = []
    if sections.metadata.references:
        meta_lines.append(f"**Références** : {sections.metadata.references}  ")
    if sections.metadata.code_aiot:
        meta_lines.append(f"**Code AIOT** : {sections.metadata.code_aiot}  ")
    if sections.metadata.date_visite:
        meta_lines.append(
            f"**Visite d'inspection** : {sections.metadata.date_visite}"
        )
    if meta_lines:
        lines.extend(meta_lines)
        lines.append("")

    if sections.contexte:
        lines.append("## 1) Contexte")
        lines.append("")
        lines.append(sections.contexte)
        lines.append("")

    if sections.subsections:
        lines.append("## 2) Constats")
        lines.append("")
        for heading, body in sections.subsections:
            lines.append(f"### {heading}")
            lines.append("")
            # Si cette sous-section contient des fiches, on les rend
            # individuellement plutôt que de recoller le body brut.
            inline_fiches = parse_fiches_constats(body)
            if inline_fiches:
                for fiche in inline_fiches:
                    lines.append(
                        f"#### Fiche N° {fiche.numero} — {fiche.titre}"
                    )
                    lines.append("")
                    lines.append(fiche.body)
                    lines.append("")
            else:
                lines.append(body)
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_generic_markdown(
    body_markdown: str,
    front_matter: FrontMatter,
) -> str:
    """Assemble un markdown à partir d'une sortie pymupdf4llm brute.

    Le ``body_markdown`` est le résultat de ``pymupdf4llm.to_markdown``
    tel quel — on ne le re-structure pas (les gabarits non DREAL sont
    trop hétérogènes pour justifier un parser dédié). On se contente
    d'ajouter le front matter YAML en tête.
    """
    return (
        render_front_matter_yaml(front_matter)
        + "\n\n"
        + body_markdown.rstrip()
        + "\n"
    )


def render_failed_markdown(
    front_matter: FrontMatter,
    error: str,
) -> str:
    """Markdown de secours pour un PDF qu'on n'a pas pu extraire.

    Écrit le front matter et une courte note d'erreur. Permet de
    garder une trace markdown cohérente avec le reste du corpus
    (1 PDF = 1 md) plutôt qu'un trou silencieux.
    """
    return (
        render_front_matter_yaml(front_matter)
        + "\n\n"
        "# Extraction impossible\n"
        "\n"
        f"_(contenu non extractible : {error})_\n"
    )


def build_pages_url_markdown(pdf_filename: str) -> str:
    """Construit l'URL GitHub Pages du markdown à partir du nom du PDF.

    ``foo.pdf`` → ``https://.../rapports-inspection-markdown/foo.md``.
    """
    if not pdf_filename.endswith(".pdf"):
        raise ValueError(
            f"build_pages_url_markdown: nom de fichier attendu *.pdf, "
            f"reçu : {pdf_filename!r}"
        )
    md_name = pdf_filename[: -len(".pdf")] + ".md"
    return PAGES_URL_TEMPLATE.format(filename=md_name)


def compute_sha256_str(content: str) -> str:
    """SHA-256 hex d'une chaîne encodée en UTF-8."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --- Phase 3 : OCR wrapper (ocrmypdf via subprocess) ----------------------

# Commande OCR : on invoque ``ocrmypdf`` via ``uvx`` pour ne pas
# imposer une installation système. L'utilisateur garde tesseract +
# pack fra installés nativement (``brew install tesseract
# tesseract-lang``), mais ``ocrmypdf`` lui-même est résolu à la volée.
#
# Arguments :
# - ``--force-ocr`` : OCRise TOUTES les pages, même celles qui ont
#   déjà un layer texte. On ne passe ici que pour les PDFs dont
#   ``needs_ocr()`` a déjà déterminé que le texte existant est
#   inutilisable (< ``SCAN_MIN_CHARS`` caractères) ; le cas BORDEAUX
#   METROPOLE est un PDF dont le layer texte fait juste 12 chars de
#   puces • et serait skippé avec ``--skip-text`` sans qu'on
#   récupère le vrai contenu scanné derrière.
# - ``--language fra+eng`` : modèle tesseract français avec
#   l'anglais en secours pour les documents bilingues et les
#   sigles importés.
# - ``--output-type pdf`` : reste en PDF standard (pas PDF/A) —
#   évite une dépendance à ghostscript pour la conversion PDF/A.
# - ``--optimize 0`` : pas d'optimisation post-OCR — idem, pas de
#   ghostscript requis.
# - ``--quiet`` : stderr uniquement pour les erreurs, sans la barre
#   de progression qui pollue les logs du run batch.
OCRMYPDF_ARGS: tuple[str, ...] = (
    "uvx",
    "ocrmypdf",
    "--force-ocr",
    "--language",
    "fra+eng",
    "--output-type",
    "pdf",
    "--optimize",
    "0",
    "--quiet",
)


class OCRError(RuntimeError):
    """Levée quand ``ocrmypdf`` échoue ou ne produit pas de couche texte.

    Encapsule le code retour du sous-processus et stderr pour aider
    au diagnostic. Le pipeline la rattrape et marque le PDF comme
    ``ExtractionMethod.FAILED`` avec le message comme raison.
    """


def has_text_layer(pdf_path: Path) -> bool:
    """Vérifie si un PDF contient assez de texte pour éviter l'OCR.

    Lit le PDF avec pymupdf et concatène le texte de toutes les
    pages, puis applique le même seuil ``SCAN_MIN_CHARS`` que
    ``needs_ocr`` — garantit que les deux fonctions sont alignées
    sur la définition de "scan".
    """
    import pymupdf  # import local : coûteux au module load

    with pymupdf.open(pdf_path) as doc:
        text_parts = [page.get_text("text") for page in doc]
    combined = "\n".join(text_parts)
    return not needs_ocr(combined)


def run_ocrmypdf(pdf_path: Path, timeout: int = OCR_TIMEOUT) -> None:
    """Applique une couche OCR à un PDF, en place, de façon atomique.

    Écrit le résultat dans un fichier temporaire adjacent
    (``<path>.ocr.tmp``) puis l'échange atomiquement avec la source
    via ``os.replace``. Si ``ocrmypdf`` plante, le tmp est nettoyé
    et le PDF original reste intact.

    Post-condition : si la fonction retourne sans exception,
    ``has_text_layer(pdf_path)`` est garanti ``True`` — sinon on lève
    ``OCRError`` pour forcer le pipeline à marquer le PDF comme
    ``FAILED``.
    """
    import os
    import subprocess

    tmp_path = pdf_path.with_suffix(pdf_path.suffix + ".ocr.tmp")
    cmd = [*OCRMYPDF_ARGS, str(pdf_path), str(tmp_path)]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OCRError(
            "ocrmypdf introuvable. Installer tesseract + pack fr "
            "(brew install tesseract tesseract-lang) puis lancer le "
            "script via uv run pour résoudre ocrmypdf."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        _cleanup_tmp(tmp_path)
        raise OCRError(
            f"ocrmypdf timeout ({timeout}s) sur {pdf_path.name}"
        ) from exc

    if completed.returncode != 0:
        _cleanup_tmp(tmp_path)
        stderr_tail = (completed.stderr or "").strip().splitlines()[-5:]
        raise OCRError(
            f"ocrmypdf a retourné {completed.returncode} sur "
            f"{pdf_path.name} : {' | '.join(stderr_tail) or '(stderr vide)'}"
        )

    if not tmp_path.exists():
        raise OCRError(
            f"ocrmypdf a retourné 0 mais le fichier tmp {tmp_path.name} "
            f"n'existe pas — état incohérent, aborté."
        )

    # Post-check : le nouveau PDF doit effectivement avoir une couche
    # texte. Si l'OCR a échoué silencieusement (rare mais possible sur
    # des scans très dégradés, ou sur des PDFs sources effectivement
    # vides — cas observé : 2 fichiers de 1 KB et 3 KB sans contenu),
    # on préfère lever plutôt qu'écraser.
    try:
        if not has_text_layer(tmp_path):
            raise OCRError(
                f"ocrmypdf a produit un PDF sans couche texte pour "
                f"{pdf_path.name} ({pdf_path.stat().st_size} octets) "
                f"— source vide, corrompue ou scan trop dégradé"
            )
    except OCRError:
        _cleanup_tmp(tmp_path)
        raise
    except Exception as exc:  # pragma: no cover — défensif
        _cleanup_tmp(tmp_path)
        raise OCRError(
            f"vérification post-OCR a planté sur {pdf_path.name} : {exc}"
        ) from exc

    # Échange atomique : l'original n'est jamais corrompu, soit on a
    # le nouveau PDF OCR'é, soit l'ancien intact.
    os.replace(tmp_path, pdf_path)


def _cleanup_tmp(tmp_path: Path) -> None:
    """Supprime un fichier tmp s'il existe, sans masquer d'exception."""
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except OSError:
            pass


# --- Phase 4 : pipeline + writer + manifest -------------------------------

# Imports des helpers partagés avec les autres scripts du pipeline.
# ``atomic_write`` : écriture tmp + os.replace (utilisé ici et Phase 5).
# ``require_columns`` : validation schéma CSV (Phase 5).
# ``merge_metadata`` : ownership coopératif sur metadonnees_colonnes.csv.
from _metadonnees_util import atomic_write, merge_metadata, require_columns  # noqa: E402


def compute_sha256(path: Path) -> str:
    """SHA-256 hex d'un fichier, en streaming pour éviter le big read.

    Les PDFs peuvent faire 10+ Mo ; on lit par chunks pour ne pas
    allouer 10 Mo à chaque appel.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_raw_text(pdf_path: Path) -> str:
    """Lit le texte natif d'un PDF via pymupdf, page par page.

    ``get_text("text")`` reconstruit les mots depuis les coordonnées
    des glyphes — gère correctement les PDFs dont l'encodage est
    char-par-char (variante DREAL qui casse ``pdftotext``). Les pages
    sont concaténées avec un saut de ligne simple ; ``clean_text``
    normalise ensuite.
    """
    import pymupdf  # import local

    with pymupdf.open(pdf_path) as doc:
        pages = [page.get_text("text") for page in doc]
    return "\n".join(pages)


def build_front_matter_from_csv(
    csv_row: dict[str, str],
    pdf_path: Path,
    method: ExtractionMethod,
    now: dt.datetime | None = None,
) -> FrontMatter:
    """Construit le front matter d'un markdown à partir d'une ligne CSV.

    ``csv_row`` doit être une ligne de ``rapports-inspection.csv``
    déjà validée contre ``RAPPORTS_CSV_COLUMNS``. ``pdf_path`` est
    ouvert pour calculer sha256 et taille — doit pointer sur le PDF
    dans son état actuel (post-OCR éventuel).

    ``now`` est injectable pour la reproductibilité des tests et
    snapshots ; le caller passe ``dt.datetime.now().replace(microsecond=0)``
    en production.
    """
    if now is None:
        now = dt.datetime.now().replace(microsecond=0)
    return FrontMatter(
        source_pdf=pdf_path.name,
        source_sha256=compute_sha256(pdf_path),
        source_bytes=pdf_path.stat().st_size,
        id_icpe=csv_row["id_icpe"],
        nom_complet=csv_row["nom_complet"],
        siret=csv_row["siret"],
        date_inspection=csv_row["date_inspection"],
        identifiant_fichier=csv_row["identifiant_fichier"],
        url_source_georisques=csv_row["url_source_georisques"],
        url_pages=csv_row["url_pages"],
        extraction_method=method.value,
        extraction_version=EXTRACTION_VERSION,
        extracted_at=now.isoformat(),
    )


def extract_pdf(
    pdf_path: Path,
    csv_row: dict[str, str],
    *,
    allow_ocr: bool = True,
    now: dt.datetime | None = None,
) -> ExtractionResult:
    """Pipeline complet d'extraction pour un PDF.

    Étapes :

    1. Lecture du texte natif via pymupdf.
    2. Si le texte est trop court (``needs_ocr``) et que ``allow_ocr``
       est ``True``, on applique ``run_ocrmypdf`` en place puis on
       relit le texte.
    3. Classification ``dreal_parser`` vs ``pymupdf4llm_generic``.
    4. Préfixage ``ocr_then_`` si OCR a été appliqué.
    5. Parsing ou conversion générique, puis rendu markdown.
    6. Post-validation : le front matter est validé contre le JSON
       Schema avant retour. Une violation lève ``RuntimeError`` car
       c'est un bug interne — nos valeurs viennent de notre propre
       code et d'un CSV qu'on contrôle.

    En cas d'échec à n'importe quelle étape, retourne un
    ``ExtractionResult`` avec ``method=FAILED`` et un markdown de
    secours contenant le front matter et le message d'erreur. Ne
    lève pas : le caller (orchestrateur) décide quoi faire.
    """
    try:
        raw = extract_raw_text(pdf_path)
    except Exception as exc:  # pymupdf peut lever sur PDF corrompu
        return _failed_result(pdf_path, csv_row, f"lecture PDF : {exc}", now)

    text = clean_text(raw)
    ocr_applied = False

    if needs_ocr(text):
        if not allow_ocr:
            return _failed_result(
                pdf_path,
                csv_row,
                "PDF scanné et --no-ocr actif",
                now,
            )
        try:
            run_ocrmypdf(pdf_path)
        except OCRError as exc:
            return _failed_result(pdf_path, csv_row, str(exc), now)
        ocr_applied = True
        try:
            raw = extract_raw_text(pdf_path)
        except Exception as exc:
            return _failed_result(
                pdf_path, csv_row, f"relecture post-OCR : {exc}", now
            )
        text = clean_text(raw)
        if needs_ocr(text):
            return _failed_result(
                pdf_path,
                csv_row,
                "texte toujours vide après OCR",
                now,
            )

    method = classify_text(text)
    if ocr_applied:
        method = _prefix_ocr(method)

    fm = build_front_matter_from_csv(csv_row, pdf_path, method, now=now)

    fiches: list[Fiche] = []
    try:
        if method in (
            ExtractionMethod.DREAL_PARSER,
            ExtractionMethod.OCR_THEN_DREAL_PARSER,
        ):
            sections = parse_dreal_sections(text)
            fiches = sections.fiches
            md = render_dreal_markdown(sections, fm)
        else:
            md = _render_via_pymupdf4llm(pdf_path, fm)
    except Exception as exc:
        return _failed_result(
            pdf_path, csv_row, f"rendu markdown : {exc}", now
        )

    return ExtractionResult(
        method=method, markdown=md, front_matter=fm, fiches=fiches
    )


def _prefix_ocr(method: ExtractionMethod) -> ExtractionMethod:
    """Passe de ``DREAL_PARSER`` à ``OCR_THEN_DREAL_PARSER`` (et pareil générique)."""
    if method == ExtractionMethod.DREAL_PARSER:
        return ExtractionMethod.OCR_THEN_DREAL_PARSER
    if method == ExtractionMethod.PYMUPDF4LLM_GENERIC:
        return ExtractionMethod.OCR_THEN_PYMUPDF4LLM
    return method  # déjà préfixé ou FAILED — ne devrait pas arriver


def _render_via_pymupdf4llm(pdf_path: Path, fm: FrontMatter) -> str:
    """Wrapper autour de ``pymupdf4llm.to_markdown`` pour le chemin générique."""
    import pymupdf4llm

    body = pymupdf4llm.to_markdown(str(pdf_path))
    return render_generic_markdown(body, fm)


def _failed_result(
    pdf_path: Path,
    csv_row: dict[str, str],
    error: str,
    now: dt.datetime | None,
) -> ExtractionResult:
    """Construit un ExtractionResult FAILED avec front matter de secours.

    Si le front matter lui-même est inconstructible (ex. CSV row
    mutilé), on retombe sur un markdown minimal sans front matter et
    ``markdown=""`` pour signaler le cas extrême au caller.
    """
    try:
        fm = build_front_matter_from_csv(
            csv_row, pdf_path, ExtractionMethod.FAILED, now=now
        )
        md = render_failed_markdown(fm, error)
    except Exception as exc:  # pragma: no cover — défensif
        return ExtractionResult(
            method=ExtractionMethod.FAILED,
            markdown="",
            front_matter=None,
            error=f"{error} (et front matter inconstructible : {exc})",
        )
    return ExtractionResult(
        method=ExtractionMethod.FAILED,
        markdown=md,
        front_matter=fm,
        error=error,
    )


def write_markdown(markdown: str, md_path: Path) -> None:
    """Écrit le markdown de façon atomique via ``atomic_write``."""
    with atomic_write(md_path) as handle:
        handle.write(markdown)


def load_manifest(path: Path) -> dict[str, ManifestEntry]:
    """Lit le manifeste append-only et retourne la dernière entrée par PDF.

    Format attendu : 1 ligne JSON par entrée (JSONL). Les lignes qui
    ne peuvent pas être décodées sont ignorées (permissif à la
    lecture, strict à l'écriture). Pour un même ``source_pdf``, la
    dernière ligne rencontrée (donc la plus récente en fin de fichier)
    fait autorité.
    """
    entries: dict[str, ManifestEntry] = {}
    if not path.exists():
        return entries
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict) or "source_pdf" not in parsed:
                continue
            entries[parsed["source_pdf"]] = parsed  # type: ignore[assignment]
    return entries


def append_manifest(path: Path, entry: ManifestEntry) -> None:
    """Ajoute une ligne JSON au manifeste, en mode append.

    Pas d'écriture atomique ici : l'append JSONL est justement conçu
    pour être reprenable — une ligne partiellement écrite est la
    dernière, et ``load_manifest`` la rejette à la lecture. Avantage :
    aucune réécriture de tout le manifeste à chaque extraction.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False)
        handle.write("\n")


def is_up_to_date(
    manifest: dict[str, ManifestEntry],
    source_pdf: str,
    source_sha256: str,
) -> bool:
    """Vrai si le manifeste atteste déjà cette extraction.

    Condition : même ``source_pdf`` ET même ``source_sha256`` ET même
    ``extraction_version``. Un bump de version invalide toutes les
    entrées précédentes (les markdowns existants sont toujours là,
    mais le re-run les ré-extrait).
    """
    entry = manifest.get(source_pdf)
    if entry is None:
        return False
    return (
        entry["source_sha256"] == source_sha256
        and entry["extraction_version"] == EXTRACTION_VERSION
    )


def build_manifest_entry(
    fm: FrontMatter,
    markdown: str,
    md_path: Path,
) -> ManifestEntry:
    """Construit une entrée de manifeste pour une extraction réussie."""
    return ManifestEntry(
        source_pdf=fm["source_pdf"],
        source_sha256=fm["source_sha256"],
        markdown_file=md_path.name,
        markdown_sha256=compute_sha256_str(markdown),
        extraction_method=fm["extraction_method"],
        extraction_version=fm["extraction_version"],
        extracted_at=fm["extracted_at"],
    )


def validate_front_matter_against_schema(
    fm: FrontMatter,
    schema: dict[str, object],
) -> None:
    """Valide un front matter contre le JSON Schema chargé.

    Lève ``RuntimeError`` sur violation, en nommant le chemin du
    champ fautif. Pas de remontée silencieuse : un mismatch ici est
    toujours un bug dans le code extracteur (les valeurs sortent
    de nos propres fonctions).
    """
    import jsonschema  # import local

    try:
        jsonschema.validate(instance=fm, schema=schema)
    except jsonschema.ValidationError as exc:
        raise RuntimeError(
            f"front matter invalide : {exc.message} "
            f"(champ : {list(exc.absolute_path)})"
        ) from exc


def load_schema() -> dict[str, object]:
    """Charge le JSON Schema draft-07 depuis ``scripts/schemas/``."""
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


# --- Sidecar _fiches.jsonl (v0.2.0) ---------------------------------------

FICHES_SIDECAR_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "fiches_sidecar.json"
)
FICHES_SIDECAR_PATH = MARKDOWN_DIR / "_fiches.jsonl"


def compute_fiches_sidecar_entry(
    pdf_path: Path,
    source_sha256: str,
    extraction_method: str,
    fiches: list[Fiche],
) -> dict[str, object]:
    """Construit une entrée sidecar pour un PDF, avec page + bbox par fiche.

    Ouvre le PDF via pymupdf pour chercher chaque titre de fiche et
    calculer la bbox réelle (du titre au titre suivant ou fin de page).

    Pour les PDFs non-DREAL (``fiches=[]``), retourne une entrée sans
    fiches — le PDF est tout de même tracé dans le sidecar pour que
    ``construire_fiches.py`` puisse générer un prose row.
    """
    import pymupdf  # import local

    with pymupdf.open(pdf_path) as doc:
        page_count = len(doc)
        fiche_entries: list[dict[str, object]] = []

        for i, fiche in enumerate(fiches):
            next_fiche = fiches[i + 1] if i + 1 < len(fiches) else None
            regions = _find_fiche_regions(
                doc,
                fiche.numero,
                fiche.titre,
                next_fiche.numero if next_fiche else None,
                next_fiche.titre if next_fiche else None,
            )
            fiche_entries.append({
                "num": fiche.numero,
                "titre": fiche.titre,
                "body": fiche.body,
                "sub_section": fiche.sub_section,
                "regions": [
                    {"page": r.page, "bbox": r.bbox}
                    for r in regions
                ],
            })

    return {
        "source_pdf": pdf_path.name,
        "source_sha256": source_sha256,
        "extraction_version": EXTRACTION_VERSION,
        "extraction_method": extraction_method,
        "page_count": page_count,
        "fiches": fiche_entries,
    }


def _find_fiche_regions(
    doc: object,
    fiche_num: str,
    fiche_titre: str,
    next_num: str | None,
    next_titre: str | None,
) -> list[FicheRegion]:
    """Trouve les régions visuelles d'une fiche dans le PDF.

    Stratégie : cherche ``N° {num} :`` sur chaque page. La bbox
    s'étend du haut du titre au haut du titre suivant (ou fin de page
    si la fiche est la dernière ou si la suivante est sur une autre
    page). Si la fiche s'étale sur plusieurs pages, une région par
    page est émise.

    Retourne ``[]`` si le titre n'est pas trouvé (OCR dégradé, PDF
    corrompu) — le client affiche alors le PDF à la page 1 en
    fallback.
    """
    search_text = f"N° {fiche_num} :"
    next_search = f"N° {next_num} :" if next_num else None

    # Trouver la page de début
    start_page_idx: int | None = None
    title_rect = None
    for page_idx in range(len(doc)):  # type: ignore[arg-type]
        page = doc[page_idx]  # type: ignore[index]
        rects = page.search_for(search_text)
        if rects:
            start_page_idx = page_idx
            title_rect = rects[0]
            break

    if start_page_idx is None or title_rect is None:
        return []

    page = doc[start_page_idx]  # type: ignore[index]
    page_rect = page.rect
    left_margin = page_rect.x0 + 30
    right_margin = page_rect.x1 - 30

    # Chercher le titre suivant sur la MÊME page
    if next_search:
        next_rects = page.search_for(next_search)
        same_page_next = [r for r in next_rects if r.y0 > title_rect.y0 + 10]
        if same_page_next:
            # La fiche suivante est sur la même page — bbox simple
            return [FicheRegion(
                page=start_page_idx + 1,
                bbox=[
                    left_margin,
                    max(0.0, title_rect.y0 - 5),
                    right_margin,
                    same_page_next[0].y0 - 5,
                ],
            )]

    # La fiche suivante n'est PAS sur la même page (ou pas de fiche suivante)
    # → la région s'étend jusqu'au bas de cette page
    regions = [FicheRegion(
        page=start_page_idx + 1,
        bbox=[
            left_margin,
            max(0.0, title_rect.y0 - 5),
            right_margin,
            page_rect.height - 30,
        ],
    )]

    # Si fiche suivante existe, chercher sur les pages suivantes
    if next_search:
        for next_page_idx in range(start_page_idx + 1, len(doc)):  # type: ignore[arg-type]
            next_page = doc[next_page_idx]  # type: ignore[index]
            next_rects = next_page.search_for(next_search)
            if next_rects:
                # La fiche suivante commence sur cette page.
                # Si la zone entre le top margin et le début de la
                # fiche suivante est trop fine (< 30 pts ≈ 0.4 inch),
                # on la supprime plutôt que de générer un snippet vide.
                end_y = next_rects[0].y0 - 5
                if end_y - 60 >= 30:
                    regions.append(FicheRegion(
                        page=next_page_idx + 1,
                        bbox=[
                            next_page.rect.x0 + 30,
                            60,
                            next_page.rect.x1 - 30,
                            end_y,
                        ],
                    ))
                break
            # Page entière appartient à cette fiche
            regions.append(FicheRegion(
                page=next_page_idx + 1,
                bbox=[
                    next_page.rect.x0 + 30,
                    60,
                    next_page.rect.x1 - 30,
                    next_page.rect.height - 30,
                ],
            ))

    return regions


def append_fiches_sidecar(path: Path, entry: dict[str, object]) -> None:
    """Ajoute une ligne JSON au sidecar _fiches.jsonl (append-only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False)
        handle.write("\n")


# --- Phase 5 : orchestrator + CLI + main ----------------------------------

import argparse  # noqa: E402
import csv  # noqa: E402
from collections import defaultdict  # noqa: E402

# Métadonnées des colonnes écrites par CE script dans rapports-inspection.csv.
# Le pattern est le même que dans telecharger_rapports_inspection.py :
# spec partagée pour générer le header aliasé ET alimenter le dictionnaire
# metadonnees_colonnes.csv via merge_metadata.
URL_MARKDOWN_COLUMN_SPEC = (
    "url_markdown",
    "url_markdown",
    "(calculé)",
    (
        "URL GitHub Pages de la version markdown du rapport "
        "(rapports-inspection-markdown/{basename}.md). Écrite par "
        "extract_rapports_markdown.py. Vide si l'extraction a échoué "
        "(voir _erreurs.log dans rapports-inspection-markdown/)."
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse les arguments CLI de l'extracteur.

    Tous les flags sont optionnels ; sans argument, le script extrait
    tous les PDFs de ``rapports-inspection/`` qui ne sont pas déjà
    à jour dans le manifeste.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Extrait les rapports d'inspection ICPE de Gironde en "
            "markdown déterministes avec front matter YAML strict."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="N'extrait que les N premiers PDFs (ordre stable par nom).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Liste ce qui serait fait sans rien écrire (ni markdown, "
            "ni manifeste, ni mise à jour du CSV)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore le manifeste et ré-extrait tous les PDFs, même "
            "ceux dont le sha256 est à jour."
        ),
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help=(
            "Ne lance pas ocrmypdf sur les scans : ils sont marqués "
            "FAILED. Utile pour les runs rapides sans dépendance "
            "tesseract."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Mode validation : n'extrait rien, relit tous les "
            "markdowns existants et vérifie leur front matter contre "
            "le JSON Schema."
        ),
    )
    parser.add_argument(
        "--only-ocr",
        action="store_true",
        help=(
            "Lance uniquement l'étape OCR sur les PDFs scannés "
            "(sans produire de markdown). Utile pour pré-OCR'er le "
            "corpus en plusieurs runs séparés."
        ),
    )
    return parser.parse_args(argv)


def check_prereqs() -> None:
    """Vérifie la présence des fichiers sources avant de démarrer.

    Exit propre (sans traceback) si un chemin est absent : un
    ``FileNotFoundError`` au milieu du run donnerait un message
    beaucoup moins actionnable.
    """
    missing: list[str] = []
    for label, path in [
        ("PDFs source", PDF_DIR),
        ("CSV rapports", RAPPORTS_CSV),
        ("JSON Schema", SCHEMA_PATH),
    ]:
        if not path.exists():
            missing.append(f"  - {label} : {path}")
    if missing:
        print(
            "[prereq] fichiers requis absents :\n" + "\n".join(missing),
            file=sys.stderr,
        )
        sys.exit(2)


def load_rapports_csv() -> list[dict[str, str]]:
    """Charge ``rapports-inspection.csv`` avec validation du schéma."""
    rows: list[dict[str, str]] = []
    with RAPPORTS_CSV.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        require_columns(reader.fieldnames, RAPPORTS_CSV_COLUMNS, RAPPORTS_CSV)
        rows.extend(reader)
    return rows


def group_by_pdf(
    rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Groupe les lignes par ``nom_fichier_local`` (dedup du cas identifiant partagé).

    Plusieurs lignes peuvent pointer vers le même PDF local (1 seul
    cas connu mais on reste générique). Les lignes dont
    ``statut_telechargement`` n'est pas ``ok`` / ``skip`` sont
    ignorées : pas de PDF sur disque, rien à extraire.
    """
    # ``require_columns`` a validé que les 2 clés sont dans les
    # fieldnames du CSV au chargement ; on peut donc accéder
    # directement sans fallback (la valeur peut être "" si la cellule
    # est vide, mais jamais KeyError).
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["statut_telechargement"] not in {"ok", "skip"}:
            continue
        filename = row["nom_fichier_local"].strip()
        if not filename:
            continue
        groups[filename].append(row)
    return dict(groups)


def pick_primary_row(group: list[dict[str, str]]) -> dict[str, str]:
    """Sélectionne la ligne "primaire" d'un groupe partageant un PDF.

    Convention identique à ``telecharger_rapports_inspection.py`` :
    tri numérique par ``id_icpe`` puis lexicographique par
    ``identifiant_fichier``, et on garde le plus petit. Garantit un
    résultat stable run-à-run.
    """
    return sorted(
        group,
        key=lambda r: (int(r["id_icpe"]), r["identifiant_fichier"]),
    )[0]


def markdown_path_for(pdf_filename: str) -> Path:
    """``foo.pdf`` → ``rapports-inspection-markdown/foo.md``."""
    if not pdf_filename.endswith(".pdf"):
        raise ValueError(
            f"markdown_path_for: nom de fichier attendu *.pdf, "
            f"reçu : {pdf_filename!r}"
        )
    return MARKDOWN_DIR / (pdf_filename[: -len(".pdf")] + ".md")


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Compte-rendu de fin de run, à afficher en résumé."""

    total: int = 0
    ok: int = 0
    skip: int = 0
    failed: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)


def run_extraction(
    args: argparse.Namespace,
    schema: dict[str, object],
) -> RunSummary:
    """Boucle principale : extrait les PDFs, écrit markdowns et manifeste.

    Retourne un ``RunSummary`` qui peut être utilisé par ``main()``
    pour afficher un résumé final et décider du code retour.
    """
    rows = load_rapports_csv()
    groups = group_by_pdf(rows)
    ordered_filenames = sorted(groups)
    if args.limit is not None:
        ordered_filenames = ordered_filenames[: args.limit]

    manifest = load_manifest(MANIFEST_PATH)
    summary = RunSummary(by_method={}, failures=[])
    summary_total = 0
    summary_ok = 0
    summary_skip = 0
    summary_failed = 0
    by_method: dict[str, int] = defaultdict(int)
    failures: list[tuple[str, str]] = []
    # ``written_filenames`` = tout markdown écrit ce run (succès ET
    # failed avec corps). On s'en sert pour remplir la colonne
    # ``url_markdown`` du CSV : un rapport "Extraction impossible"
    # reste une cible utile (le lecteur tombe sur les métadonnées +
    # la raison + les URLs sources) plutôt qu'un lien vide.
    written_filenames: set[str] = set()

    now = dt.datetime.now().replace(microsecond=0)

    for index, filename in enumerate(ordered_filenames, start=1):
        summary_total += 1
        pdf_path = PDF_DIR / filename
        if not pdf_path.exists():
            failures.append((filename, "PDF absent du dossier rapports-inspection"))
            summary_failed += 1
            continue

        primary = pick_primary_row(groups[filename])
        md_path = markdown_path_for(filename)

        if args.only_ocr:
            _only_ocr_pass(pdf_path, args.no_ocr, failures)
            continue

        # Idempotence : saute si le manifest atteste déjà une extraction
        # à jour (même sha256 ET même extraction_version) ET que le
        # fichier markdown est toujours présent sur disque. Si le
        # markdown a été supprimé manuellement, on le régénère même
        # si le manifeste dit "à jour".
        if not args.force and md_path.exists():
            current_sha = compute_sha256(pdf_path)
            if is_up_to_date(manifest, filename, current_sha):
                summary_skip += 1
                if args.dry_run:
                    print(f"[{index:4d}/{len(ordered_filenames)}] skip  {filename}")
                continue

        if args.dry_run:
            print(f"[{index:4d}/{len(ordered_filenames)}] plan  {filename}")
            continue

        result = extract_pdf(
            pdf_path, primary, allow_ocr=not args.no_ocr, now=now
        )
        by_method[result.method.value] += 1

        # Validation du front matter contre le schema avant écriture :
        # coupe court à un markdown invalide qui passerait en CSV et
        # en manifeste.
        if result.front_matter is not None:
            try:
                validate_front_matter_against_schema(
                    result.front_matter, schema
                )
            except RuntimeError as exc:
                failures.append((filename, f"schema: {exc}"))
                summary_failed += 1
                print(
                    f"[{index:4d}/{len(ordered_filenames)}] SCHEMA {filename}: {exc}",
                    file=sys.stderr,
                )
                continue

        # Écrit le markdown dans tous les cas (y compris FAILED) pour
        # garder 1 PDF = 1 .md dans le corpus. Sans markdown, une
        # analyse downstream ne saurait pas que le PDF existait.
        if result.markdown:
            write_markdown(result.markdown, md_path)
            written_filenames.add(filename)

        # Le manifeste stocke AUSSI les FAILED, avec leur raison
        # d'échec. Conséquence : au prochain run, ``is_up_to_date``
        # matche pour ce PDF à ce sha256 et à cette extraction_version
        # et on saute l'OCR (coûteux) pour les PDFs vides / corrompus
        # qu'on sait ne pas pouvoir récupérer. Un ``--force`` ou un
        # bump d'``extraction_version`` déclenche bien une nouvelle
        # tentative quand on a amélioré l'extracteur.
        if result.front_matter is not None:
            entry = build_manifest_entry(
                result.front_matter, result.markdown, md_path
            )
            append_manifest(MANIFEST_PATH, entry)
            manifest[filename] = entry

        # Sidecar _fiches.jsonl : 1 entrée par PDF, avec les fiches
        # structurées + page + bbox. Écrit pour TOUS les PDFs (même
        # FAILED et generic sans fiches) pour que construire_fiches.py
        # puisse générer des prose rows.
        if result.front_matter is not None:
            sidecar_entry = compute_fiches_sidecar_entry(
                pdf_path,
                result.front_matter["source_sha256"],
                result.method.value,
                result.fiches,
            )
            append_fiches_sidecar(FICHES_SIDECAR_PATH, sidecar_entry)

        if result.method in SUCCESS_METHODS:
            summary_ok += 1
            print(
                f"[{index:4d}/{len(ordered_filenames)}] {result.method.value:<22} {filename}"
            )
        else:
            summary_failed += 1
            error_msg = result.error or "(raison inconnue)"
            failures.append((filename, error_msg))
            print(
                f"[{index:4d}/{len(ordered_filenames)}] FAILED {filename}: {error_msg}",
                file=sys.stderr,
            )

    # Pour la colonne url_markdown du CSV, on veut TOUTE ligne dont
    # le markdown existe sur disque — pas seulement celles écrites
    # pendant ce run. Sinon un run partiel (--limit) effacerait les
    # url_markdown des lignes skippées, puisque le code ne reconstruit
    # la colonne qu'à partir de ce qu'il a écrit ce run. On fusionne
    # les écritures du run + les markdowns préexistants sur disque.
    existing_on_disk = {
        md_path.stem + ".pdf"
        for md_path in MARKDOWN_DIR.glob("*.md")
    }
    url_markdown_filenames = written_filenames | existing_on_disk

    if not args.dry_run and not args.only_ocr:
        _update_rapports_csv(rows, url_markdown_filenames)
        _merge_url_markdown_metadata()
        _write_error_log(failures)

    return RunSummary(
        total=summary_total,
        ok=summary_ok,
        skip=summary_skip,
        failed=summary_failed,
        by_method=dict(by_method),
        failures=failures,
    )


def _only_ocr_pass(
    pdf_path: Path,
    no_ocr: bool,
    failures: list[tuple[str, str]],
) -> None:
    """Mode ``--only-ocr`` : OCR les scans sans écrire de markdown."""
    if no_ocr:
        return
    try:
        if has_text_layer(pdf_path):
            return
    except Exception as exc:  # PDF corrompu
        failures.append((pdf_path.name, f"lecture pré-OCR : {exc}"))
        return
    try:
        run_ocrmypdf(pdf_path)
        print(f"[ocr-only] {pdf_path.name}")
    except OCRError as exc:
        failures.append((pdf_path.name, f"ocr: {exc}"))
        print(f"[ocr-only] FAILED {pdf_path.name}: {exc}", file=sys.stderr)


def _update_rapports_csv(
    rows: list[dict[str, str]],
    markdown_filenames: set[str],
) -> None:
    """Ajoute/remplace la colonne ``url_markdown`` dans rapports-inspection.csv.

    Une ligne reçoit un ``url_markdown`` si son ``nom_fichier_local``
    est présent dans ``markdown_filenames`` (qui inclut à la fois les
    markdowns écrits ce run ET ceux déjà présents sur disque des runs
    précédents). Les lignes partageant un PDF (cas identifiant partagé)
    héritent toutes du même lien, comme pour ``url_pages``.

    Les rapports ``extraction_method: failed`` ont aussi un
    ``url_markdown`` : le fichier ``.md`` correspondant existe, contient
    les métadonnées et la raison de l'échec — cliquer dessus reste
    plus utile qu'un lien vide.
    """
    # Préserve TOUTES les colonnes existantes + ajoute url_markdown
    # juste après url_pages pour la lisibilité.
    with RAPPORTS_CSV.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        existing_fieldnames = list(reader.fieldnames or [])

    # Position d'insertion : juste après url_pages, ou en fin si absent.
    if "url_markdown" in existing_fieldnames:
        new_fieldnames = existing_fieldnames
    elif "url_pages" in existing_fieldnames:
        idx = existing_fieldnames.index("url_pages") + 1
        new_fieldnames = (
            existing_fieldnames[:idx]
            + ["url_markdown"]
            + existing_fieldnames[idx:]
        )
    else:
        new_fieldnames = [*existing_fieldnames, "url_markdown"]

    # ``nom_fichier_local`` est garanti par ``require_columns`` au
    # chargement. ``url_markdown`` en revanche peut être absente si
    # c'est le premier run sur ce CSV — on utilise ``setdefault`` pour
    # préserver la valeur existante des runs précédents quand le
    # markdown est toujours là, et écrire "" seulement quand la clé
    # manque.
    for row in rows:
        filename = row["nom_fichier_local"].strip()
        if filename and filename in markdown_filenames:
            row["url_markdown"] = build_pages_url_markdown(filename)
        else:
            row.setdefault("url_markdown", "")

    with atomic_write(RAPPORTS_CSV) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=new_fieldnames, quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(rows)

    # Après la boucle ci-dessus chaque ligne a forcément la clé
    # (setdefault s'en est occupée quand elle manquait), on peut
    # donc accéder directement.
    total_with_url = sum(1 for row in rows if row["url_markdown"])
    print(
        f"[csv] rapports-inspection.csv mis à jour "
        f"({len(rows)} lignes, {total_with_url} url_markdown au total)"
    )


def _merge_url_markdown_metadata() -> None:
    """Ajoute la ligne ``url_markdown`` au dictionnaire multi-fichiers."""
    _alias, alias, nom_original, definition = URL_MARKDOWN_COLUMN_SPEC
    owner_rows = [
        {
            "fichier": RAPPORTS_OUTPUT_FILENAME,
            "nom_original": nom_original,
            "alias": alias,
            "definition": definition,
        }
    ]
    merge_metadata(METADATA_CSV, RAPPORTS_OUTPUT_FILENAME, owner_rows)


def _write_error_log(failures: list[tuple[str, str]]) -> None:
    """Écrit ``_erreurs.log`` avec la liste humaine des échecs du run."""
    if not failures:
        if ERREURS_LOG.exists():
            ERREURS_LOG.unlink()
        return
    lines = [
        "# Échecs d'extraction du dernier run",
        "",
        f"date : {dt.datetime.now().isoformat(timespec='seconds')}",
        f"nombre d'échecs : {len(failures)}",
        "",
    ]
    for filename, reason in failures:
        lines.append(f"- {filename}")
        lines.append(f"    raison : {reason}")
    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    with atomic_write(ERREURS_LOG) as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"[log] {len(failures)} échecs écrits dans {ERREURS_LOG.name}")


def run_validation(schema: dict[str, object]) -> int:
    """Mode ``--validate`` : valide tous les front matter existants.

    Parcourt tous les .md du dossier markdown, extrait leur front
    matter, le valide contre le schéma. Retourne 0 si tous valides,
    1 sinon. Affiche en stderr chaque markdown défaillant avec la
    raison précise.
    """
    if not MARKDOWN_DIR.exists():
        print(
            f"[validate] dossier {MARKDOWN_DIR} absent — rien à valider",
            file=sys.stderr,
        )
        return 0
    markdowns = sorted(MARKDOWN_DIR.glob("*.md"))
    if not markdowns:
        print("[validate] aucun markdown à valider", file=sys.stderr)
        return 0
    invalid: list[tuple[str, str]] = []
    for md_path in markdowns:
        fm_dict = _parse_front_matter_block(md_path.read_text(encoding="utf-8"))
        if fm_dict is None:
            invalid.append((md_path.name, "front matter absent ou malformé"))
            continue
        try:
            validate_front_matter_against_schema(fm_dict, schema)  # type: ignore[arg-type]
        except RuntimeError as exc:
            invalid.append((md_path.name, str(exc)))
    total = len(markdowns)
    valid = total - len(invalid)
    print(f"[validate] {valid}/{total} markdowns valides")
    if invalid:
        for name, reason in invalid[:20]:
            print(f"  - {name}: {reason}", file=sys.stderr)
        if len(invalid) > 20:
            print(f"  ... et {len(invalid) - 20} autres", file=sys.stderr)
        return 1
    return 0


def _parse_front_matter_block(markdown_text: str) -> dict[str, object] | None:
    """Extrait le bloc front matter YAML d'un markdown et le reparse.

    Format attendu : première ligne ``---``, clés ``key: value``
    jusqu'à un second ``---``. Les valeurs sont décodées via
    ``json.loads`` (car ``render_front_matter_yaml`` les encode via
    ``json.dumps``, ce qui garantit que tout JSON valide est accepté).

    Retourne ``None`` si le bloc est absent ou mal formé — le caller
    (``run_validation``) le compte comme une erreur explicite.
    """
    lines = markdown_text.splitlines()
    if not lines or lines[0] != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    result: dict[str, object] = {}
    for line in lines[1:end]:
        if not line or ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            continue
        try:
            result[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            # Valeur non-JSON (ex. entier non quoté) : accepte telle quelle.
            try:
                result[key] = int(raw_value)
            except ValueError:
                result[key] = raw_value
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    check_prereqs()
    schema = load_schema()

    if args.validate:
        return run_validation(schema)

    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)

    summary = run_extraction(args, schema)

    print()
    print("=== résumé ===")
    print(f"total   : {summary.total}")
    print(f"ok      : {summary.ok}")
    print(f"skip    : {summary.skip}")
    print(f"failed  : {summary.failed}")
    if summary.by_method:
        print("par méthode :")
        for method, count in sorted(summary.by_method.items()):
            print(f"  {method:<24} {count}")

    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
