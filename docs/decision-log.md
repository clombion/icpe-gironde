# Journal des décisions

Les choix méthodologiques du cahier d'enquête ICPE, organisés par thème. Chaque entrée documente une décision qui affecte la conception, les limites ou la reproductibilité du travail.

La première section, **Décisions en attente**, liste les choix identifiés mais pas encore tranchés — avec la question, le contournement actuel, ce qui débloquerait la décision, et la conséquence du contournement. Quand une décision en attente est tranchée, elle rejoint la section thématique appropriée et la ligne en attente est supprimée.

---

## 0. Décisions en attente

### Fiches vides du pivot (TASK-14)

**Question :** Les 478 fiches dont `constats_body` est vide ou ≤ 10 caractères doivent-elles être filtrées dans `construire_fiches.py`, ou corrigées en amont dans l'extraction ?
**Contournement actuel :** Exclues du tagging LLM uniquement ; elles restent dans le pivot et le sqlite.
**Ce qui débloque :** Vérification d'un échantillon contre les PDFs source — fiches réellement vides, ou défaut du parser DREAL ?
**Conséquence du contournement :** Ces lignes polluent la recherche plein texte et les statistiques d'angles.

---

## 1. Sources et acquisition

### Deux sources ICPE, une seule canonique

**Situation :** Un export bulk officiel (API Géorisques V1, 2 890 lignes) et un snapshot historique data.gouv.fr (2 888 lignes, février 2025) coexistent.
**Décision :** L'export officiel est la source de référence. Le snapshot n'est conservé que pour récupérer les dates de création, absentes de l'export officiel.
**Justification :** Une seule source de vérité évite les conflits de valeurs ; le snapshot apporte un champ que l'officiel n'a pas.

### Archivage horodaté avec empreinte

**Situation :** L'export bulk change à chaque refresh.
**Décision :** Chaque exécution de `fetch_georisques.py` archive le ZIP horodaté dans `données-georisques/raw/` et écrit URL + sha256 dans `PROVENANCE.txt`, plus un diff automatique contre le CSV précédent.
**Justification :** Reproductibilité — toute analyse peut être rattachée à l'état exact des données qui l'a produite.

### Politesse envers le serveur Géorisques

**Situation :** 1 784 PDFs à télécharger.
**Décision :** Batches de 3 téléchargements parallèles, 0,5 s de pause entre batches. Les 404 durables sont mémorisés dans `_404.txt` et jamais retentés ; les échecs transitoires (5xx, réseau) le sont au run suivant.
**Justification :** Ne pas surcharger un service public ; distinguer échec définitif et transitoire rend le script idempotent et reprenable.

---

## 2. Extraction des rapports

### Routage par type de PDF

**Situation :** Les PDFs d'inspection mélangent gabarit DREAL structuré (~91 %), courriers au format libre (~9 %) et scans sans couche texte (~3 %).
**Décision :** Classification automatique vers `dreal_parser` (sections sémantiques, fiches de constat en H4 indexables), `pymupdf4llm_generic` (conversion générique), ou OCR préalable (`ocrmypdf --force-ocr`, fait en place de façon atomique) puis routage normal.
**Justification :** Le gabarit DREAL permet une extraction structurée fine ; forcer un parser unique perdrait soit la structure, soit les documents atypiques.

### Manifeste append-only et idempotence

**Situation :** L'extraction de 1 782 PDFs doit être relançable sans tout refaire.
**Décision :** `_manifest.jsonl` append-only trace chaque extraction avec `source_sha256`, `markdown_sha256` et version du script. Un PDF déjà extrait au bon sha et à la bonne version est skippé.
**Justification :** L'idempotence par empreinte (pas par présence de fichier) garantit qu'un markdown correspond bien au PDF et à la version d'extraction déclarés.

### Pivot des fiches : 7 champs DREAL + validation par ligne

**Situation :** Les fiches de constat suivent un gabarit à 7 champs labélisés (Thème, Type de suites, Référence réglementaire, Prescription, Constats, Proposition de suites, Déjà contrôlé).
**Décision :** `construire_fiches.py` parse ces 7 champs, joint les métadonnées installation, et valide chaque ligne contre un JSON Schema strict. Les 393 lignes non structurables sont conservées comme « prose rows » à côté des 10 599 fiches structurées.
**Justification :** La validation par ligne bloque la dérive du parser ; garder les prose rows évite de perdre du contenu au motif qu'il ne rentre pas dans le gabarit.

---

## 3. Audit des coordonnées

### Cascade de géocodage BAN → OpenCage → Nominatim

**Situation :** Vérifier l'accord entre coordonnées enregistrées et adresse postale pour 2 890 sites.
**Décision :** Géocodage forward en cascade : BAN d'abord (3 stratégies d'adresse), puis OpenCage (couvre les lieux-dits et châteaux que BAN n'indexe pas), puis Nominatim en dernier recours (limité à 1 req/s). Sans clé OpenCage, l'étape est silencieusement sautée.
**Justification :** Chaque géocodeur a un domaine de force différent ; l'ordre va du plus précis sur l'adressage français au plus générique.

### Les corrections sont un sidecar, jamais une édition de la source

**Situation :** Les revues humaines produisent des corrections de coordonnées.
**Décision :** Les verdicts (4 valeurs : `garder_stored`, `utiliser_geocoded`, `placer_manuellement`, `terrain`) sont exportés en JSON par bucket, commités dans `coordonnees-audit-reviews/`, compilés par `apply_corrections.py` en un sidecar CSV que l'enrichisseur applique. Les CSV source ne sont jamais modifiés à la main.
**Justification :** La source reste re-téléchargeable et diffable ; toute correction est traçable à une revue signée et réversible en supprimant le sidecar.

### Tri en trois groupes de revue

**Situation :** Trop de sites flagués pour une revue uniforme.
**Décision :** Trois groupes priorisés : **reserves** (l'écart change la réponse « dans une réserve naturelle ? » — la question centrale de l'enquête), **grand** (≥ 500 m, mauvaise commune, structurel), **petit** (25–500 m).
**Justification :** L'effort humain se concentre là où l'erreur change une conclusion d'enquête.

---

## 4. Tagging thématique

### Taxonomie inductive avant tagging déductif

**Situation :** Classifier 10 514 fiches de constat sans grille préexistante.
**Décision :** Analyse thématique (Braun & Clarke) sur un échantillon stratifié de 146 fiches → taxonomie v5 gelée (6 axes, 78 codes) + codebook, validée sur 659 fiches (0 % M15, 11 % d'erreurs corrigées par la passe Skeptic) avant tout tagging à l'échelle.
**Justification :** La taxonomie émerge du corpus au lieu d'y être plaquée ; la validation chiffrée sur 659 fiches établit la fiabilité de l'instrument avant de payer le tagging complet.

### Pattern Hunter/Skeptic, audit à 100 %

**Situation :** Le tagging LLM d'un corpus complet dérive sans contrôle.
**Décision :** Deux passes par batch de 130 fiches : un Hunter agressif (M15 = 0 % visé), puis un Skeptic qui re-lit et corrige. Pour cette première passe complète, le Skeptic audite 100 % des batches.
**Justification :** Le Hunter seul sur-classifie ; le taux de correction mesuré (7,2 % agrégé, cible < 15 %) sert à la fois de critère de sortie et de détecteur de dérive par batch.

### Le modèle fait partie de l'instrument calibré

**Situation :** Choisir le modèle des agents de tagging.
**Décision :** Sonnet pour Hunter et Skeptic — le même modèle que la validation des 659 fiches et que les 81 batches Hunter. TASK-10 (script de production pour les nouvelles fiches) a été aligné de Haiku vers Sonnet pour la même raison.
**Justification :** Les statistiques de calibration valent pour le triple prompt + taxonomie + modèle ; changer de modèle en cours de campagne rendrait les batches incomparables.

### Tags dans un parquet séparé, joint au build

**Situation :** Où stocker les tags par rapport au pivot.
**Décision :** `fiches-tags.parquet` séparé, joint à `fiches.parquet` par `build_sqlite.py` au moment de la projection sqlite. Le pivot source n'est jamais modifié.
**Justification :** Supprimer le fichier de tags suffit à revenir en arrière ; le pipeline automatisé peut régénérer les tags sans toucher à la source.

### Filtrage SQL : colonnes simples indexées + JSON arrays

**Situation :** Exposer 6 axes de tags dans les filtres web.
**Décision :** Les axes mono-valeur (gravité, trajectoire, dynamique) deviennent des colonnes indexées ; les axes multi-label (domaines, mécanismes) sont stockés en JSON array string, parsés côté client.
**Justification :** Filtrage SQL natif sur les axes principaux sans faire exploser le schéma pour les axes multi-label.

### Traitement des alias hors taxonomie : trois classes, pas de règle générale

**Situation :** Les agents ont produit des codes hors taxonomie (~300 occurrences, voir BUG-004) : déplacements d'axe mécaniques (`m_DELAI`/`m_MENACE` dans `mechanisms`, ×270), un alias de suffixe prouvé (`M04_MED`, ×12), et ~16 codes sémantiquement ambigus — surtout des suffixes de mécanisme miroités en domaines (`D09_PAC` ← `M09_PAC`, `D17_CLASSIFICATION` ← `M17_CLASSIFICATION`).
**Décision :** Pas de normalisation par préfixe généralisée. Trois traitements : (1) prompt v1.1 avec règles de validité stricte — les hunter des batches non audités sont corrigés par la passe Skeptic elle-même ; (2) `scripts/fix_tag_aliases.py` pour les deux seules corrections prouvées non ambiguës (déplacement d'axe `m_*`, `M04_MED` → `M04_MISE_EN_DEMEURE`) ; (3) ré-adjudication par relecture de fiche (agent Sonnet) pour les 6 entrées ambiguës des finals. Chaque passe est tracée dans `outputs-fiches/tags/_provenance.jsonl`.
**Justification :** La normalisation par préfixe aurait mal étiqueté les cas ambigus (`D09_PAC` → `D09_BIODIVERSITE` alors que PAC = porter-à-connaissance → `D12_ADMIN` ; `D10_GARANTIES` → `D10_ELECTRIQUE_ESP` alors que les garanties financières sont administratives). En dessous d'une vingtaine de cas, relire les fiches coûte moins cher que faire confiance à une règle astucieuse.

### Le merge refuse les batches non audités

**Situation :** `merge_tags.py` acceptait silencieusement la sortie Hunter brute pour tout batch sans final Skeptic.
**Décision :** Refus par défaut avec la liste des batches manquants ; `--allow-hunter-fallback` est l'opt-in explicite.
**Justification :** Un avertissement console ne protège pas un artefact publié — un merge partiel doit être un choix, pas un accident (voir BUG-005).

---

## 5. Publication et infrastructure

### Site statique sans framework ni build

**Situation :** Quatre outils web pour des journalistes, hébergés gratuitement.
**Décision :** GitHub Pages, HTML/CSS/JS purs, aucune étape de build. La page Vérifier utilise sql.js (SQLite WASM, ~1 Mo) sur `fiches.sqlite` pré-indexé.
**Justification :** Zéro infrastructure à maintenir, contribution possible par simple PR, et le moteur SQL complet tourne dans le navigateur.

### URLs Pages inscrites dans les données

**Situation :** Chaque rapport PDF et markdown a une URL GitHub Pages, stockée dans les CSV et le pivot (`url_pages`, `url_markdown`).
**Décision :** Assumé — les URLs sont régénérées par le pipeline. Conséquence documentée : tout changement de dépôt ou de compte impose une réécriture globale + reconstruction du pivot (fait le 2026-07-17 pour la migration vers `clombion/icpe-gironde`, 1 795 fichiers ; voir BUG-006).
**Justification :** Les URLs pré-calculées évitent toute logique de construction d'URL côté client et rendent les CSV exploitables hors du site.

### Artefacts régénérables dans `local/`

**Situation :** Le corpus taggable (10 514 fichiers, 45 Mo) et les tranches de batch sont entièrement régénérables depuis le pivot.
**Décision :** Ils vivent dans `local/` (gitignoré), avec `_paths.py` comme source de vérité des chemins. Les artefacts LLM payés (tags, taxonomie, codebook, prompts) restent versionnés.
**Justification :** Le dépôt ne porte que ce qui est coûteux ou impossible à régénérer ; le reste se reconstruit par `extract_corpus.py`.

### Historique git réinitialisé à la restauration

**Situation :** Le projet était un module git d'un dépôt parent perdu avec la machine ; 4 jours de travail (taxonomie, 81 batches Hunter, 33 finals) n'existaient que dans le backup.
**Décision :** Nouveau dépôt autonome `clombion/icpe-gironde`, premier commit = snapshot du backup (état 2026-04-14). L'ancien historique (compte bononlouis-del) est orphelin et non migré.
**Justification :** Le contenu restauré était plus avancé que le remote ; un snapshot honnête vaut mieux qu'une greffe d'historique incomplète. Leçon tirée dans BUG-001.
