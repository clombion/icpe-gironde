# ICPE en Gironde — cahier d'enquête

Quatre outils d'enquête sur les **Installations Classées pour la
Protection de l'Environnement** (ICPE) en Gironde :
**carte interactive**, **audit des coordonnées**,
**catalogue des données**, et **rapports d'inspection**.

Le site est déployé sur **GitHub Pages** — tous les outils sont
utilisables directement dans le navigateur, sans installation :

| Outil | URL | Description |
|---|---|---|
| **Accueil** | [bononlouis-del.github.io/…/](https://bononlouis-del.github.io/Les-ICPE-en-r-serve-naturelle-nationale/) | Page d'entrée avec les 4 cartes |
| **Carte interactive** | [/carte/](https://bononlouis-del.github.io/Les-ICPE-en-r-serve-naturelle-nationale/carte/) | 2 890 ICPE filtrables, réserves naturelles, ortho-photo IGN |
| **Audit des coordonnées** | [/audit/](https://bononlouis-del.github.io/Les-ICPE-en-r-serve-naturelle-nationale/audit/) | Revue collaborative par bucket — mini-carte, verdicts, export JSON |
| **Tableau des décisions** | [/audit/table.html](https://bononlouis-del.github.io/Les-ICPE-en-r-serve-naturelle-nationale/audit/table.html) | Vue tabulaire de toutes les décisions — filtres, tri, export CSV |
| **Catalogue des données** | [/donnees/](https://bononlouis-del.github.io/Les-ICPE-en-r-serve-naturelle-nationale/donnees/) | Dictionnaire des colonnes avec échantillons cliquables |
| **Rapports d'inspection** | [/rapports/](https://bononlouis-del.github.io/Les-ICPE-en-r-serve-naturelle-nationale/rapports/) | 10 599 fiches de constat — recherche plein texte, analyse par angle |

## Ce que la carte permet

- Visualiser 2 890 installations classées en Gironde, colorées selon
  le régime, le niveau Seveso, la priorité nationale, l'IED ou le
  secteur.
- Filtrer par combinaison de critères (recherche, régime, Seveso,
  priorité, IED, secteur) avec recalcul instantané.
- Parcourir un instantané temporel mensuel via un curseur : voir quels
  dossiers ICPE étaient actifs à une date donnée.
- Basculer l'affichage du contour du département, des communes, des
  Réserves Naturelles Nationales et Régionales.
- Ouvrir directement la fiche Géorisques de chaque site.

## Audit des coordonnées

L'outil `/audit/` permet de revoir tous les ICPE où les coordonnées
enregistrées et l'adresse postale ne sont pas d'accord. Le but est
d'identifier les sites où le désaccord change la réponse à
*« ce site est-il dans une réserve naturelle ? »* — la question
centrale du projet (cf. ALMA SCI / Marais de Bruges).

`scripts/audit_coordinates.py` exécute 5 passes de signaux par site :

1. **Sentinelles offline** — null_island, outside_gironde,
   commune_centroid, duplicate_coords
2. **Point-in-polygon commune** — vérification tristate contre
   `carte/data/gironde-communes.geojson`
3. **Géocodage forward en cascade** — BAN (3 stratégies adresse1 /
   adresse2 / combinées) → OpenCage (couvre les lieux-dits / châteaux
   que BAN n'indexe pas) → Nominatim (dernier recours pour ce qu'OpenCage
   a dégradé en commune-level)
4. **Géocodage reverse** — adresse au point enregistré, sert à détecter
   wrong_commune et à confirmer la commune pour les address_unresolvable
5. **Appartenance aux réserves** — point-in-polygon RNN/RNR + distance
   à la limite, calcule `reserve_ambiguous`

Les sites flagués sont triés en 3 groupes pour la revue :
**reserves** (cas critiques pour les réserves), **grand** (≥500m,
commune incorrecte, ou structurel), **petit** (25-500m).

L'outil de revue `/audit/` charge `flagged.json`, présente une mini-carte
par site (CartoDB Voyager + IGN ortho-photo en option), et permet à
chaque revieweur d'enregistrer un verdict (`garder_stored`,
`utiliser_geocoded`, `placer_manuellement`, `terrain`) puis d'exporter
le bucket en JSON. Les revues commitées dans
`données-georisques/audit/coordonnees-audit-reviews/` sont découvertes
automatiquement via la GitHub Contents API.

```bash
# Lancer l'audit (avec ou sans clé OpenCage)
OPENCAGE_API_KEY=... uv run scripts/audit_coordinates.py
# Sans la clé, OpenCage est silencieusement skipé.
```

Cache des géocodeurs : `données-georisques/audit/.cache/` (gitignored).
Re-runs incrémentaux — seules les nouvelles lignes hitent les API.

## Catalogue des données

L'outil `/donnees/` rend `metadonnees_colonnes.csv` lisible par un
humain : une section par fichier, table de colonnes avec définition,
type inféré, et jusqu'à 5 valeurs d'échantillon cliquables-pour-copier.

`scripts/build_metadata_samples.py` (stdlib only) régénère le sidecar
`carte/data/metadonnees_samples.json` après chaque mise à jour des
données :

```bash
python3 scripts/build_metadata_samples.py
```

## Rapports d'inspection

L'outil `/rapports/` offre 3 sous-pages pour exploiter les rapports
d'inspection ICPE extraits de Géorisques :

- **Vérifier** (`/rapports/`) — recherche plein texte via DuckDB WASM
  sur les 10 992 lignes du pivot `fiches.parquet` (10 599 fiches
  structurées + 393 prose rows), avec snippet PDF cropé à la bbox de
  chaque fiche via PDF.js (desktop) ou lien direct (mobile)
- **Analyser par angle** (`/rapports/angles.html`) — 5 requêtes SQL
  prédéfinies avec export CSV en un clic. Contributions via PR (1
  fichier `.md` par angle dans `rapports/angles/`)
- **Méthodologie** (`/rapports/methodologie.html`) — documentation
  complète du pipeline d'extraction (5 étapes, statistiques de
  couverture, limitations connues, instructions de reproduction)

Le pivot est construit par `scripts/construire_fiches.py` qui lit le
sidecar `_fiches.jsonl` (produit par `extract_rapports_markdown.py`
v0.2.0), parse les 7 champs labélisés DREAL (Thème, Type de suites,
Référence réglementaire, Prescription, Constats, Proposition de suites,
Déjà contrôlé) et joint les métadonnées installation. Validation
per-row contre un JSON Schema strict.

```bash
# Extraire les markdowns + sidecar
uv run scripts/extract_rapports_markdown.py

# Construire le pivot
uv run scripts/construire_fiches.py

# Reconstruire l'index des angles (si ajout/édition d'un angle .md)
python3 scripts/build_angles_index.py
```

## Sources de données

| Donnée | Source |
|---|---|
| Liste ICPE Gironde (manuelle, géométries) | [data.gouv.fr — export Géorisques](https://www.data.gouv.fr/) |
| Bulk ICPE Gironde (canonique) | [API Géorisques V1](https://www.georisques.gouv.fr/doc-api) |
| Contour Gironde | [geo.api.gouv.fr](https://geo.api.gouv.fr/decoupage-administratif) |
| Communes Gironde | [geo.api.gouv.fr](https://geo.api.gouv.fr/decoupage-administratif) |
| Réserves Naturelles Nationales | [IGN Géoplateforme (WFS patrinat_rnn)](https://data.geopf.fr/wfs/ows) |
| Réserves Naturelles Régionales | [IGN Géoplateforme (WFS patrinat_rnr)](https://data.geopf.fr/wfs/ows) |

Deux sources ICPE coexistent :

- `carte/liste-icpe-gironde.csv` (2 888 lignes) — snapshot historique
  data.gouv.fr (février 2025), conservé pour récupérer les dates de
  création absentes de l'export officiel.
- `données-georisques/` — export bulk officiel de l'API Géorisques V1
  pour le département 33, canonique. ZIP archivé horodaté dans
  `raw/` (sha256 dans `PROVENANCE.txt`), éclaté en cinq CSV normalisés
  reliés par `codeAiot` : installations, inspections, rapports
  d'inspection, documents hors inspection (arrêtés, rapports publics,
  mises en demeure), rubriques ICPE.

Depuis avril 2026, `scripts/enrichir_libelles.py` part de l'**export
officiel** (2 890 lignes) comme source de référence. Il désambiguïse les
noms en doublon (`structure`, `etablissement`, `nom_complet`), standardise
les catégories (régime ICPE, Seveso, booléens), génère les coordonnées
GeoJSON, et récupère les dates de création depuis le snapshot historique
quand elles existent. Le fichier produit
`carte/data/liste-icpe-gironde_enrichi.csv` (2 890 lignes) est ce que
la carte charge.

Par-dessus, `scripts/telecharger_rapports_inspection.py` télécharge les
rapports d'inspection publiables depuis Géorisques (1 784 PDFs), les
renomme de façon déterministe à partir du libellé désambiguïsé, les
stocke dans `rapports-inspection/` et produit
`carte/data/rapports-inspection.csv` avec une URL GitHub
Pages pour chaque rapport. Le fichier enrichi reçoit une colonne
supplémentaire `nb_rapports_inspection` comptant les rapports
disponibles par installation.

Ensuite, `scripts/extract_rapports_markdown.py` convertit ces PDFs en
fichiers markdown déterministes dans `rapports-inspection-markdown/`,
avec un front matter YAML strict validé par JSON Schema. Chaque
markdown est classifié vers l'un des chemins suivants :

- **`dreal_parser`** (≈ 91 %) — PDF reconnu comme gabarit DREAL
  Nouvelle-Aquitaine, parsé en sections sémantiques (Contexte,
  Constats, Fiches de constat N° X comme H4 indexables).
- **`pymupdf4llm_generic`** (≈ 9 %) — PDF texte au gabarit non DREAL
  (courriers, propositions de suites), converti via `pymupdf4llm`.
- **`ocr_then_dreal_parser`** / **`ocr_then_pymupdf4llm`** (≈ 3 %) —
  scan sans couche texte, OCRisé via `ocrmypdf --force-ocr --language
  fra+eng` puis routé vers le parser correspondant. L'OCR est fait
  en place (atomique via tmp + os.replace).

Un `_manifest.jsonl` append-only trace chaque extraction avec
`source_sha256` et `markdown_sha256`, ce qui garantit l'idempotence :
un PDF déjà extrait au bon sha et à la bonne version du script est
skippé. La colonne `url_markdown` du CSV rapports pointe vers la
version markdown GitHub Pages.

Le dictionnaire des colonnes (schéma multi-fichiers : `fichier`,
`nom_original`, `alias`, `definition`) est dans
`carte/data/metadonnees_colonnes.csv`. Il décrit les
colonnes de `liste-icpe-gironde_enrichi.csv` **et** celles de
`rapports-inspection.csv`, chaque script du pipeline possédant ses
propres lignes via le helper partagé `scripts/_metadonnees_util.py`.

Les données des réserves naturelles sont pré-traitées (filtre
bounding-box Gironde) par `carte/scripts/prep_reserves.py`.

## Structure du dépôt

```
├── README.md
├── scripts/                       # pipeline Géorisques + audit + extraction markdown
│   ├── _paths.py                           # constantes de chemin partagées (single source of truth)
│   ├── _metadonnees_util.py                # helper partagé pour le dictionnaire multi-fichiers
│   ├── fetch_georisques.py                 # téléchargement + extraction bulk officiel
│   ├── enrichir_libelles.py                # enrichissement + projection vers la carte
│   ├── telecharger_rapports_inspection.py  # téléchargement des PDFs d'inspection
│   ├── extract_rapports_markdown.py        # extraction markdown des PDFs (pymupdf + ocrmypdf)
│   ├── audit_coordinates.py                # audit des écarts coords/adresses (BAN+OpenCage+Nominatim cascade)
│   ├── build_metadata_samples.py           # sidecar d'échantillons pour /donnees/
│   ├── apply_corrections.py               # compile les revues d'audit → sidecar de corrections
│   ├── construire_fiches.py                # construit fiches.parquet depuis les sidecars
│   ├── build_angles_index.py              # scanne rapports/angles/*.md → index.json
│   ├── schemas/
│   │   ├── markdown_frontmatter.json       # JSON Schema draft-07 du front matter YAML
│   │   ├── fiches_sidecar.json             # JSON Schema du sidecar _fiches.jsonl
│   │   └── fiche.json                      # JSON Schema d'une ligne du pivot parquet
│   └── tests/                              # tests stdlib + uv (unittest discover)
├── données-georisques/            # source canonique API Géorisques V1
│   ├── raw/                       # archives ZIP datées (traçabilité sha256)
│   ├── InstallationClassee.csv    # installations (brut)
│   ├── InstallationClassee_enrichi.csv
│   ├── inspection.csv             # historique des inspections
│   ├── metadataFichierInspection.csv
│   ├── metadataFichierHorsInspection.csv
│   ├── rubriqueIC.csv             # rubriques ICPE classées
│   ├── PROVENANCE.txt             # URL + sha256 du ZIP source
│   ├── diff_report.txt            # diff bulk ↔ CSV manuel (automatique)
│   ├── diff_analysis.md           # investigation humaine des écarts
│   └── audit/                     # produits de l'audit des coordonnées
│       ├── coordonnees-audit-full.csv      # toutes les installations + colonnes audit
│       ├── coordonnees-audit-summary.md    # bilan lisible (histogrammes, top offenders)
│       ├── coordonnees-audit-flagged.json  # consommé par /audit/
│       ├── coordonnees-audit-reviews/      # revues commitées par les enquêteurs
│       └── .cache/                         # caches des géocodeurs (gitignored)
├── rapports-inspection/           # PDFs d'inspection téléchargés depuis Géorisques
│   ├── *.pdf                      # nommés {slug}_{id_icpe}_{date}_{siret}.pdf
│   ├── _404.txt                   # mémoire des identifiants définitivement 404
│   └── _erreurs.log               # rapport du dernier run (durables + transitoires)
├── rapports-inspection-markdown/  # versions markdown des PDFs (1 .md par PDF)
│   ├── *.md                       # front matter YAML + corps sémantique
│   ├── _fiches.jsonl              # sidecar structuré (fiches + page + bbox par PDF)
│   ├── _manifest.jsonl            # provenance append-only (sha256, version, timestamp)
│   └── _erreurs.log               # rapport des extractions failed du dernier run
├── index.html                     # page d'accueil — 4 cards
├── style.css                      # styles de la page d'accueil
├── shared/                        # design system partagé
│   ├── tokens.css                 # @font-face + design tokens (palette, typographie)
│   └── fonts/                     # Fraunces + IBM Plex (WOFF2)
├── carte/                         # outil 1 : carte interactive
│   ├── index.html
│   ├── app.js                     # logique de la carte
│   ├── style.css                  # styles spécifiques à la carte
│   ├── liste-icpe-gironde.csv     # snapshot historique data.gouv.fr (cdate)
│   ├── data/
│   │   ├── liste-icpe-gironde_enrichi.csv  # consommé par la carte (2 890 lignes)
│   │   ├── rapports-inspection.csv         # 1 ligne par rapport, URL Pages + statut téléchargement
│   │   ├── metadonnees_colonnes.csv        # dictionnaire multi-fichiers (fichier, nom_original, alias, definition)
│   │   ├── metadonnees_samples.json        # sidecar d'échantillons pour /donnees/
│   │   ├── fiches.parquet                 # pivot fiches de constat (10 992 lignes)
│   │   ├── fiches-meta.json              # count + versions + sha (<1 KB)
│   │   ├── fiches-manifest.jsonl         # provenance du pivot (append-only)
│   │   ├── reserves-naturelles-nationales.geojson
│   │   └── reserves-naturelles-regionales.geojson
│   └── scripts/                   # prep_reserves.py, build_epci_outlines.py, fetch_fonts.sh
├── audit/                         # outil 2 : revue d'audit des coordonnées
│   ├── index.html
│   ├── table.html                 # vue tabulaire des décisions (filtrable, exportable)
│   ├── app.js                     # state machine + Contents API + mini-map
│   ├── lib.js                     # fonctions pures (testées dans test.html)
│   ├── table.js                   # chargement + rendu du tableau des décisions
│   ├── style.css
│   └── test.html                  # tests JS dans le navigateur (~30 console.assert)
├── rapports/                      # outil 4 : rapports d'inspection
│   ├── index.html                 # page de vérification (DuckDB WASM + PDF.js snippet)
│   ├── app.js                     # search + hash routing + PDF crop
│   ├── lib.js                     # fonctions pures (testées dans test.html)
│   ├── style.css
│   ├── test.html                  # tests JS (~20 console.assert)
│   ├── angles.html                # recipe book SQL + export CSV
│   ├── angles.js
│   ├── angles/                    # 1 fichier .md par angle d'analyse
│   │   ├── 01-top-mises-en-demeure.md
│   │   ├── ...
│   │   └── index.json             # produit par build_angles_index.py
│   ├── methodologie.html          # doc méthodologie (rendu depuis .md via marked.js)
│   ├── methodologie.md            # source markdown de la méthodologie
│   └── methodologie.js
└── donnees/                       # outil 3 : catalogue des données
    ├── index.html
    ├── app.js
    └── style.css
```

## Rafraîchir les données

Les scripts 1 à 3 ne dépendent que de la stdlib Python 3.11+. Les
scripts 4, 5 et 8 ont des dépendances tierces déclarées dans chaque
fichier et résolues automatiquement par `uv run`. Pour l'OCR des scans,
installer Tesseract une fois : `brew install tesseract tesseract-lang`
(macOS).

```bash
# 1. Télécharge et extrait le bulk officiel, archive le ZIP, écrit le diff
python3 scripts/fetch_georisques.py

# 2. Recalcule structure / etablissement / nom_complet + commune / EPCI
python3 scripts/enrichir_libelles.py

# 3. Télécharge les rapports d'inspection PDF, renomme, indexe avec URL Pages
python3 scripts/telecharger_rapports_inspection.py

# 4. Convertit les PDFs d'inspection en markdown avec front matter YAML
uv run scripts/extract_rapports_markdown.py

# 5. Construit le tableau de fiches (fiches.parquet) depuis les sidecars
uv run scripts/construire_fiches.py

# 6. Reconstruit l'index des angles d'analyse (si ajout/édition d'un angle .md)
python3 scripts/build_angles_index.py

# 7. Audit des écarts coordonnées ↔ adresses (BAN + OpenCage + Nominatim)
OPENCAGE_API_KEY=... uv run scripts/audit_coordinates.py

# 8. Régénère le sidecar d'échantillons pour /donnees/
python3 scripts/build_metadata_samples.py

# 9. Compile les décisions de revue et applique les corrections à la carte
python3 scripts/apply_corrections.py
# Le script écrit coordonnees-corrections.csv puis relance automatiquement
# enrichir_libelles.py pour mettre à jour le CSV de la carte.
# Prévisualisation sans écriture : python3 scripts/apply_corrections.py --dry-run
# Écrire le sidecar sans relancer l'enrichisseur : --no-enrich

# Flags utiles du script 3 :
#   --limit 5   : test progressif sur 5 PDFs
#   --dry-run   : calcule le plan sans rien écrire ni télécharger

# Flags utiles du script 4 :
#   --limit 10  : test progressif sur 10 PDFs
#   --dry-run   : liste ce qui serait fait
#   --force     : ignore le manifeste et ré-extrait tout
#   --no-ocr    : marque les scans FAILED au lieu d'appeler ocrmypdf
#   --validate  : relit tous les .md et valide leur front matter
#   --only-ocr  : pré-OCRise les scans sans écrire de markdown
```

Chaque exécution de `fetch_georisques.py` archive un nouveau ZIP horodaté
dans `données-georisques/raw/` et met à jour `PROVENANCE.txt` et
`diff_report.txt`.

`telecharger_rapports_inspection.py` est **idempotent** : il ne
retéléchargera pas un PDF déjà présent dans `rapports-inspection/`,
donc une interruption (Ctrl-C) est reprise proprement au rerun. Les
téléchargements sont parallélisés par batches de 3 avec 0.5 s de
pause entre batches (politesse envers le serveur Géorisques). Les
échecs durables (HTTP 404) sont mémorisés dans
`rapports-inspection/_404.txt` pour ne pas être retentés, les échecs
transitoires (5xx, réseau, timeout) le seront au prochain run.

`extract_rapports_markdown.py` est **idempotent** grâce au manifeste
append-only `rapports-inspection-markdown/_manifest.jsonl` : un PDF
déjà extrait au bon `source_sha256` et à la bonne `extraction_version`
est skippé. Les scans sans texte sont OCRisés en place une seule fois.
Le run complet sur 1 782 PDFs écrit 1 780 markdowns exploitables
(91 % gabarit DREAL structuré, 9 % autre format) et 2 markdowns
`failed` pour des PDFs source effectivement vides.

Les tests :

```bash
# Tests unitaires (stdlib uniquement, pas de deps)
python3 -m unittest discover scripts/tests

# Suite complète (intégration + schema, requiert uv)
uv run --with jsonschema --with pymupdf --with pymupdf4llm --with duckdb \
    -m unittest discover scripts/tests
```

- **Réserves naturelles** : `uv run carte/scripts/prep_reserves.py`
- **Polices** : `bash carte/scripts/fetch_fonts.sh`

## Pile technique

- Leaflet 1.9 (canvas renderer) + Leaflet.markercluster
- PapaParse (worker mode) pour le CSV
- `@turf/simplify` pour alléger le contour des communes à la volée
- Polices auto-hébergées : Fraunces (display), IBM Plex Sans (UI), IBM Plex
  Mono (données)
- Pas de framework, pas de build, pas de bundler. Page statique pure.

## Licence

Code : MIT. Données : voir les sources respectives (Etalab / Licence Ouverte
pour les couches IGN et geo.api.gouv.fr ; conditions Géorisques pour le
fichier ICPE).
